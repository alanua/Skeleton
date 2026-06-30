from __future__ import annotations

import json
import sys
import time
from copy import deepcopy
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


MEMPALACE_RESULT_SCHEMA = "skeleton.mempalace_result.v1"


class MemPalaceAdapterError(ValueError):
    """Raised when the local read-only MemPalace pilot fails closed."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


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
