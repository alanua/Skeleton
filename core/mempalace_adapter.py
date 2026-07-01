from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from core.mempalace_projection import (
    MEMPALACE_SYNTHETIC_NAMESPACE,
    MEMPALACE_SYNTHETIC_PROJECT_ID,
    MemPalaceProjection,
    MemPalaceProjectionError,
    bounded_excerpt,
    build_index_manifest,
    load_projection,
    projection_terms,
    query_terms,
)
from core.private_memory_history import bytes_hash, canonical_json, utc_now


MEMPALACE_RESULT_SCHEMA = "skeleton.mempalace_result.v1"
LOCAL_MEMPALACE_INDEX_SCHEMA = "skeleton.private_memory_stack.mempalace_index.v1"


class MemPalaceAdapterError(ValueError):
    """Raised when the local read-only MemPalace pilot fails closed."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class LocalMemPalaceIndex:
    """Local-private derived semantic index built only from active SQLite facts."""

    def __init__(self, index_path: str | Path) -> None:
        self.index_path = Path(index_path)
        self._index = self._load()

    @classmethod
    def rebuild_from_facts(
        cls,
        index_path: str | Path,
        *,
        facts: list[Mapping[str, Any]],
        canonical_revision: int,
    ) -> dict[str, object]:
        documents = []
        for fact in facts:
            value = fact["value"]
            text = _flatten_text(value)
            canonical_ref = str(fact["canonical_ref"])
            documents.append(
                {
                    "item_id": _item_id(canonical_ref),
                    "canonical_ref": canonical_ref,
                    "namespace": str(fact["namespace"]),
                    "fact_id": str(fact["fact_id"]),
                    "canonical_revision": int(fact["canonical_revision"]),
                    "source_attribution": {
                        "canonical_ref": canonical_ref,
                        "canonical_revision": int(fact["canonical_revision"]),
                        "source_kind": "canonical_sqlite",
                        "value_hash": str(fact["value_hash"]),
                    },
                    "tokens": sorted(set(projection_terms(text))),
                    "text": text,
                }
            )
        payload = {
            "schema": LOCAL_MEMPALACE_INDEX_SCHEMA,
            "authoritative": False,
            "authority_classification": "derived_semantic",
            "current_canonical_revision": canonical_revision,
            "indexed_canonical_revision": canonical_revision,
            "indexed_at": utc_now(),
            "item_count": len(documents),
            "documents": sorted(documents, key=lambda item: str(item["canonical_ref"])),
        }
        payload["index_hash"] = bytes_hash(canonical_json(payload).encode("utf-8"))
        atomic_write_json_private(Path(index_path), payload)
        return cls.status(index_path, current_canonical_revision=canonical_revision)

    @staticmethod
    def status(index_path: str | Path, *, current_canonical_revision: int) -> dict[str, object]:
        path = Path(index_path)
        if not path.is_file():
            return {"state": "STALE", "indexed_canonical_revision": 0, "item_count": 0}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _validate_local_mempalace_index(data)
            indexed = int(data.get("indexed_canonical_revision", 0))
            state = "READY" if indexed == current_canonical_revision else "STALE"
            return {
                "state": state,
                "indexed_canonical_revision": indexed,
                "item_count": int(data.get("item_count", 0)),
                "authoritative": False,
            }
        except Exception:
            return {"state": "BLOCKED", "indexed_canonical_revision": 0, "item_count": 0, "authoritative": False}

    def search(self, *, query: str, limit: int = 5) -> dict[str, object]:
        try:
            terms = set(query_terms(query))
        except MemPalaceProjectionError as exc:
            raise MemPalaceAdapterError(exc.reason_code, str(exc)) from exc
        limit = _bounded_limit(limit)
        scored = []
        for document in self._index["documents"]:
            tokens = set(document["tokens"])
            matches = terms & tokens
            if not matches:
                continue
            score = round(len(matches) / len(terms), 3)
            scored.append(
                (
                    -score,
                    str(document["canonical_ref"]),
                    {
                        "schema": MEMPALACE_RESULT_SCHEMA,
                        "authoritative": False,
                        "authority_classification": "derived_semantic",
                        "result_refs": [document["canonical_ref"]],
                        "canonical_ref": document["canonical_ref"],
                        "canonical_revision": document["canonical_revision"],
                        "score": score,
                        "source_attribution": [deepcopy(document["source_attribution"])],
                        "indexed_canonical_revision": self._index["indexed_canonical_revision"],
                        "current_canonical_revision": self._index["current_canonical_revision"],
                        "stale": self._index["indexed_canonical_revision"] != self._index["current_canonical_revision"],
                        "bounded_text": bounded_excerpt(str(document["text"])),
                    },
                )
            )
        return {
            "schema": "skeleton.private_memory_stack.semantic_search.v1",
            "authoritative": False,
            "authority_classification": "derived_semantic",
            "confirmation_required": "exact_get_reads_canonical_sqlite",
            "results": [item for _, _, item in sorted(scored)[:limit]],
        }

    def _load(self) -> dict[str, Any]:
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        _validate_local_mempalace_index(data)
        return data


class MemPalaceAdapter:
    """Local-only synthetic read adapter behind MemoryGateway."""

    def __init__(self, projection_data: Mapping[str, Any]) -> None:
        started = time.perf_counter()
        try:
            self._projection = load_projection(projection_data)
        except MemPalaceProjectionError as exc:
            raise MemPalaceAdapterError(exc.reason_code, str(exc)) from exc
        self._manifest = build_index_manifest(self._projection)
        self._build_ms = int((time.perf_counter() - started) * 1000)

    @property
    def projection(self) -> MemPalaceProjection:
        return self._projection

    @property
    def manifest(self) -> dict[str, object]:
        return deepcopy(self._manifest)

    def search_semantic(
        self,
        *,
        namespace: str,
        project_id: str,
        query: str,
        limit: int = 5,
        current_canonical_revision: int | None = None,
    ) -> dict[str, object]:
        self._authorize_scope(namespace=namespace, project_id=project_id)
        terms = set(query_terms(query))
        limit = _bounded_limit(limit)
        current_revision = current_canonical_revision or self._projection.current_canonical_revision

        scored = []
        for document in self._projection.documents:
            if document.deleted:
                continue
            haystack = set(projection_terms(" ".join((document.title, document.bounded_text, *document.tags))))
            matches = terms & haystack
            if not matches:
                continue
            score = round(len(matches) / len(terms), 3)
            stale = document.canonical_revision != current_revision
            scored.append(
                (
                    -score,
                    document.item_id,
                    {
                        "schema": MEMPALACE_RESULT_SCHEMA,
                        "authoritative": False,
                        "namespace": self._projection.namespace,
                        "project_id": self._projection.project_id,
                        "result_refs": [document.item_id, document.canonical_ref],
                        "source_attribution": deepcopy(list(document.source_attribution)),
                        "score": score,
                        "indexed_canonical_revision": document.canonical_revision,
                        "current_canonical_revision": current_revision,
                        "source_snapshot_id": self._projection.source_snapshot_id,
                        "indexed_at": self._projection.indexed_at,
                        "stale": stale,
                        "bounded_text": bounded_excerpt(document.bounded_text),
                    },
                )
            )

        results = [item for _, _, item in sorted(scored)[:limit]]
        return {
            "schema": "skeleton.mempalace_search_response.v1",
            "namespace": self._projection.namespace,
            "project_id": self._projection.project_id,
            "authoritative": False,
            "results": results,
        }

    def get_index_freshness(
        self,
        *,
        namespace: str,
        project_id: str,
        current_canonical_revision: int | None = None,
    ) -> dict[str, object]:
        self._authorize_scope(namespace=namespace, project_id=project_id)
        indexed_revision = max(
            document.canonical_revision for document in self._projection.documents if not document.deleted
        )
        current_revision = current_canonical_revision or self._projection.current_canonical_revision
        return {
            "indexed_canonical_revision": indexed_revision,
            "current_canonical_revision": current_revision,
            "source_snapshot_id": self._projection.source_snapshot_id,
            "indexed_at": self._projection.indexed_at,
            "stale": indexed_revision != current_revision,
            "index_namespace": self._projection.namespace,
            "project_id": self._projection.project_id,
            "authoritative": False,
        }

    def delete_item(self, item_id: str) -> "MemPalaceAdapter":
        replacement = _projection_to_dict(self._projection)
        for document in replacement["documents"]:
            if document["item_id"] == item_id:
                document["deleted"] = True
                return MemPalaceAdapter(replacement)
        raise MemPalaceAdapterError("ITEM_NOT_FOUND", "item not found")

    def rebuild_manifest(self) -> dict[str, object]:
        return build_index_manifest(load_projection(_projection_to_dict(self._projection)))

    def resource_report(self) -> dict[str, int]:
        serialized_projection = json.dumps(_projection_to_dict(self._projection), sort_keys=True)
        serialized_manifest = json.dumps(self._manifest, sort_keys=True)
        return {
            "aggregate_disk_bytes": len(serialized_projection.encode("utf-8"))
            + len(serialized_manifest.encode("utf-8")),
            "aggregate_ram_bytes": sys.getsizeof(serialized_projection) + sys.getsizeof(serialized_manifest),
            "aggregate_build_ms": self._build_ms,
        }

    def _authorize_scope(self, *, namespace: str, project_id: str) -> None:
        if namespace != MEMPALACE_SYNTHETIC_NAMESPACE or project_id != MEMPALACE_SYNTHETIC_PROJECT_ID:
            raise MemPalaceAdapterError("MEMPALACE_SCOPE_NOT_AUTHORIZED", "scope is not authorized")


def _bounded_limit(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > 10:
        raise MemPalaceAdapterError("INVALID_LIMIT", "limit must be an integer from 1 to 10")
    return value


def _projection_to_dict(projection: MemPalaceProjection) -> dict[str, object]:
    return {
        "schema": "skeleton.mempalace_projection.v1",
        "namespace": projection.namespace,
        "project_id": projection.project_id,
        "source_snapshot_id": projection.source_snapshot_id,
        "current_canonical_revision": projection.current_canonical_revision,
        "indexed_at": projection.indexed_at,
        "documents": [
            {
                "item_id": document.item_id,
                "canonical_ref": document.canonical_ref,
                "canonical_revision": document.canonical_revision,
                "title": document.title,
                "bounded_text": document.bounded_text,
                "tags": list(document.tags),
                "source_attribution": deepcopy(list(document.source_attribution)),
                "deleted": document.deleted,
            }
            for document in projection.documents
        ],
    }


def _flatten_text(value: Any) -> str:
    if isinstance(value, Mapping):
        parts = []
        for key in sorted(value):
            if str(key).startswith("_"):
                continue
            parts.append(str(key))
            parts.append(_flatten_text(value[key]))
        return " ".join(part for part in parts if part)
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _item_id(canonical_ref: str) -> str:
    return "mempalace-" + bytes_hash(canonical_ref.encode("utf-8"))[:24]


def _validate_local_mempalace_index(data: object) -> Mapping[str, Any]:
    if not isinstance(data, Mapping) or data.get("schema") != LOCAL_MEMPALACE_INDEX_SCHEMA:
        raise MemPalaceAdapterError("INVALID_LOCAL_INDEX", "local MemPalace index schema is invalid")
    documents = data.get("documents")
    if not isinstance(documents, list):
        raise MemPalaceAdapterError("INVALID_LOCAL_INDEX", "local MemPalace documents must be an array")
    if int(data.get("item_count", -1)) != len(documents):
        raise MemPalaceAdapterError("INVALID_LOCAL_INDEX", "local MemPalace item count is invalid")
    expected_hash = data.get("index_hash")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise MemPalaceAdapterError("INVALID_LOCAL_INDEX", "local MemPalace index hash is invalid")
    without_hash = dict(data)
    without_hash.pop("index_hash", None)
    actual_hash = bytes_hash(canonical_json(without_hash).encode("utf-8"))
    if actual_hash != expected_hash:
        raise MemPalaceAdapterError("INVALID_LOCAL_INDEX", "local MemPalace index hash mismatch")
    return data


def atomic_write_json_private(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
        tmp.chmod(0o600)
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        if tmp.exists():
            tmp.unlink()
