from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.cognee_projection_adapter import CogneeProjectionAdapter


MEMORY_BOOTSTRAP_REQUEST_SCHEMA = "skeleton.memory_bootstrap.request.v1"
MEMORY_BOOTSTRAP_RESPONSE_SCHEMA = "skeleton.memory_bootstrap.response.v1"
MEMORY_BOOTSTRAP_RECEIPT_SCHEMA = "skeleton.memory_bootstrap.receipt.v1"
PRIVATE_CONTEXT_ENV = "SKELETON_RUNNER_PRIVATE_CONTEXT_FILE"
GENERIC_PUBLIC_REASON = "internal_error"

_READY = "READY"
_PUBLIC_REASON_ALLOWLIST = frozenset(
    {
        "ok",
        "no_private_memory_config",
        "semantic_unavailable",
        "semantic_stale",
        "semantic_scope_mismatch",
        "semantic_canonical_mismatch",
        "mempalace_unavailable",
        "mempalace_stale",
        "graphify_unavailable",
        "graphify_stale",
        "private_echo_blocked",
        "private_context_write_failed",
        GENERIC_PUBLIC_REASON,
    }
)
_GENERIC_WORDS = frozenset(
    {
        "summary",
        "document",
        "documents",
        "record",
        "records",
        "memory",
        "context",
        "project",
        "dataset",
        "canonical",
        "revision",
        "result",
        "results",
        "status",
        "ready",
        "stale",
        "blocked",
        "private",
        "public",
        "operator",
        "runner",
        "executor",
    }
)


@dataclass(frozen=True)
class RetainedMemoryBootstrap:
    private_config_hash: str
    canonical_revision: int
    semantic_candidates: tuple[object, ...]
    primary_semantic_layer: str


_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[str, int], RetainedMemoryBootstrap] = {}


def retained_memory_bootstrap(
    private_config: Mapping[str, Any] | None = None,
    *,
    current_canonical_revision: int = 0,
) -> RetainedMemoryBootstrap:
    """Return a revision-bound retained bootstrap with Cognee first.

    The default Cognee adapter uses the local package backend and may honestly
    report UNAVAILABLE until a later approved runtime configuration exists.
    """

    config = dict(private_config or {})
    config_hash = _hash_json(_jsonable(config))
    revision = _revision(current_canonical_revision)
    key = (config_hash, revision)
    with _CACHE_LOCK:
        retained = _CACHE.get(key)
        if retained is None:
            retained = RetainedMemoryBootstrap(
                private_config_hash=config_hash,
                canonical_revision=revision,
                semantic_candidates=(CogneeProjectionAdapter(),),
                primary_semantic_layer="cognee",
            )
            _CACHE[key] = retained
        return retained


def build_private_context_payload(
    *,
    gateway: object,
    project_id: str,
    dataset_id: str,
    query: str,
    canonical_keys: tuple[str, ...],
    current_canonical_revision: int,
    cognee_adapter: object | None = None,
    mempalace_adapter: object | None = None,
    mempalace_status: Mapping[str, Any] | None = None,
    graphify_adapter: object | None = None,
    graphify_status: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    canonical_records = {
        key: _exact_payload(
            gateway.lookup_exact(namespace=project_id, project_id=project_id, key=key)
        )
        for key in canonical_keys
    }
    selected_semantic = None
    semantic_reason = "semantic_unavailable"
    if cognee_adapter is not None:
        selected_semantic, semantic_reason = _select_cognee(
            cognee_adapter=cognee_adapter,
            gateway=gateway,
            project_id=project_id,
            dataset_id=dataset_id,
            query=query,
            current_canonical_revision=current_canonical_revision,
            canonical_records=canonical_records,
        )
    if selected_semantic is None and mempalace_adapter is not None:
        selected_semantic, semantic_reason = _select_mempalace(
            mempalace_adapter=mempalace_adapter,
            mempalace_status=mempalace_status,
            gateway=gateway,
            project_id=project_id,
            query=query,
            current_canonical_revision=current_canonical_revision,
            canonical_records=canonical_records,
        )

    selected_graph, graph_reason = _select_graphify(
        graphify_adapter=graphify_adapter,
        graphify_status=graphify_status,
        gateway=gateway,
        project_id=project_id,
        query=query,
        current_canonical_revision=current_canonical_revision,
        canonical_records=canonical_records,
    )

    return {
        "schema": MEMORY_BOOTSTRAP_RESPONSE_SCHEMA,
        "project_id": project_id,
        "dataset_id": dataset_id,
        "current_canonical_revision": current_canonical_revision,
        "canonical_exact_records": canonical_records,
        "selected_context": {
            "semantic_layer": selected_semantic,
            "semantic_reason": safe_reason(semantic_reason),
            "graph_layer": selected_graph,
            "graph_reason": safe_reason(graph_reason),
        },
    }


def write_private_context_payload(payload: Mapping[str, Any], directory: str | Path) -> Path:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".runner-private-context.", suffix=".json", dir=root)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            handle.write("\n")
        tmp.chmod(0o600)
        final = root / "runner-private-context.json"
        os.replace(tmp, final)
        final.chmod(0o600)
        return final
    finally:
        if tmp.exists():
            tmp.unlink()


def public_receipt(payload: Mapping[str, Any]) -> dict[str, object]:
    selected = payload.get("selected_context") if isinstance(payload, Mapping) else {}
    selected_map = selected if isinstance(selected, Mapping) else {}
    semantic = selected_map.get("semantic_layer")
    graph = selected_map.get("graph_layer")
    exact_records = payload.get("canonical_exact_records")
    return {
        "schema": MEMORY_BOOTSTRAP_RECEIPT_SCHEMA,
        "status": "DONE",
        "aggregate_counts": {
            "canonical_exact_record_count": len(exact_records) if isinstance(exact_records, Mapping) else 0,
            "semantic_result_count": _result_count(semantic),
            "graph_result_count": _result_count(graph),
        },
        "selected_layers": {
            "semantic": _layer_name(semantic),
            "graph": _layer_name(graph),
        },
        "reason_codes": [
            safe_reason(selected_map.get("semantic_reason")),
            safe_reason(selected_map.get("graph_reason")),
        ],
    }


def safe_reason(reason: object) -> str:
    if isinstance(reason, str) and reason in _PUBLIC_REASON_ALLOWLIST:
        return reason
    return GENERIC_PUBLIC_REASON


def private_echo_detected(text: str, private_values: object) -> bool:
    for value in _specific_strings(private_values):
        if value in text:
            return True
    return False


def sanitize_public_text_before_write(text: str, private_values: object) -> str:
    if private_echo_detected(text, private_values):
        return "BLOCKED: reason=private_echo_blocked"
    return text


def _specific_strings(value: object) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for text in _nested_strings(value):
        normalized = " ".join(text.split())
        if normalized in seen or not _is_distinctive_private_string(normalized):
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _select_cognee(
    *,
    cognee_adapter: object,
    gateway: object,
    project_id: str,
    dataset_id: str,
    query: str,
    current_canonical_revision: int,
    canonical_records: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, object] | None, str]:
    try:
        health = cognee_adapter.health(
            project_id=project_id,
            dataset_id=dataset_id,
            current_canonical_revision=current_canonical_revision,
        )
        revisions = health.get("canonical_revisions") if isinstance(health, Mapping) else None
        if (
            not isinstance(revisions, Mapping)
            or health.get("status") != _READY
            or revisions.get("indexed_canonical_revision") != current_canonical_revision
            or revisions.get("current_canonical_revision") != current_canonical_revision
        ):
            return None, "semantic_stale"
        response = cognee_adapter.recall(
            {
                "schema": "skeleton.semantic_memory.recall_request.v1",
                "project_id": project_id,
                "dataset_id": dataset_id,
                "query": query,
                "current_canonical_revision": current_canonical_revision,
                "limit": 5,
            }
        )
        if not _validate_derived_results(
            response,
            gateway=gateway,
            project_id=project_id,
            current_canonical_revision=current_canonical_revision,
            canonical_records=canonical_records,
            require_dataset_id=dataset_id,
        ):
            return None, "semantic_canonical_mismatch"
        return {"layer": "cognee", "response": deepcopy(response)}, "ok"
    except Exception:
        return None, "semantic_unavailable"


def _select_mempalace(
    *,
    mempalace_adapter: object,
    mempalace_status: Mapping[str, Any] | None,
    gateway: object,
    project_id: str,
    query: str,
    current_canonical_revision: int,
    canonical_records: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, object] | None, str]:
    try:
        status = dict(mempalace_status or mempalace_adapter.get_index_freshness(
            namespace=project_id,
            project_id=project_id,
            current_canonical_revision=current_canonical_revision,
        ))
        if not _ready_at_revision(status, current_canonical_revision):
            return None, "mempalace_stale"
        response = mempalace_adapter.search_semantic(
            namespace=project_id,
            project_id=project_id,
            query=query,
            current_canonical_revision=current_canonical_revision,
        )
        if not _validate_derived_results(
            response,
            gateway=gateway,
            project_id=project_id,
            current_canonical_revision=current_canonical_revision,
            canonical_records=canonical_records,
        ):
            return None, "semantic_canonical_mismatch"
        return {"layer": "mempalace", "status": status, "response": deepcopy(response)}, "ok"
    except Exception:
        return None, "mempalace_unavailable"


def _select_graphify(
    *,
    graphify_adapter: object | None,
    graphify_status: Mapping[str, Any] | None,
    gateway: object,
    project_id: str,
    query: str,
    current_canonical_revision: int,
    canonical_records: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, object] | None, str]:
    if graphify_adapter is None:
        return None, "graphify_unavailable"
    try:
        status = dict(graphify_status or graphify_adapter.get_index_freshness(
            namespace=project_id,
            project_id=project_id,
            current_canonical_revision=current_canonical_revision,
        ))
        if not _ready_at_revision(status, current_canonical_revision):
            return None, "graphify_stale"
        response = graphify_adapter.query_code(namespace=project_id, project_id=project_id, query=query)
        if not _validate_derived_results(
            response,
            gateway=gateway,
            project_id=project_id,
            current_canonical_revision=current_canonical_revision,
            canonical_records=canonical_records,
        ):
            return None, "semantic_canonical_mismatch"
        return {"layer": "graphify", "status": status, "response": deepcopy(response)}, "ok"
    except Exception:
        return None, "graphify_unavailable"


def _validate_derived_results(
    response: object,
    *,
    gateway: object,
    project_id: str,
    current_canonical_revision: int,
    canonical_records: Mapping[str, Mapping[str, Any]],
    require_dataset_id: str | None = None,
) -> bool:
    if not isinstance(response, Mapping):
        return False
    if response.get("project_id") not in {None, project_id}:
        return False
    if require_dataset_id is not None and response.get("dataset_id") not in {None, require_dataset_id}:
        return False
    if response.get("current_canonical_revision", current_canonical_revision) != current_canonical_revision:
        return False
    if response.get("indexed_canonical_revision", current_canonical_revision) != current_canonical_revision:
        return False
    for result in response.get("results", []):
        if not isinstance(result, Mapping):
            return False
        if result.get("project_id") not in {None, project_id}:
            return False
        metadata = result.get("metadata")
        if require_dataset_id is not None:
            if not isinstance(metadata, Mapping) or metadata.get("dataset_id") != require_dataset_id:
                return False
        canonical_ref = str(result.get("canonical_ref") or result.get("canonical_ref_hint") or "")
        if not canonical_ref:
            refs = result.get("result_refs")
            if isinstance(refs, list):
                canonical_ref = next((str(ref) for ref in refs if str(ref).startswith("canon-")), "")
        exact = _find_exact_record(canonical_records, canonical_ref)
        if exact is None:
            exact = _exact_by_ref(gateway, project_id, canonical_ref)
        if exact is None:
            return False
        if result.get("canonical_revision", result.get("canonical_revision_hint")) != exact.get("canonical_revision"):
            return False
        if result.get("canonical_revision", current_canonical_revision) != current_canonical_revision:
            return False
        if not _hash_matches(result, exact):
            return False
        if not _provenance_matches(result, exact):
            return False
    return True


def _ready_at_revision(status: Mapping[str, Any], current_revision: int) -> bool:
    return (
        status.get("state") == _READY
        and status.get("indexed_canonical_revision") == current_revision
        and status.get("current_canonical_revision", current_revision) == current_revision
    )


def _exact_payload(response: Mapping[str, Any]) -> dict[str, Any]:
    payload = response.get("payload") if isinstance(response, Mapping) else None
    if not isinstance(payload, Mapping):
        raise ValueError("gateway exact response missing payload")
    return dict(deepcopy(payload))


def _exact_by_ref(gateway: object, project_id: str, canonical_ref: str) -> Mapping[str, Any] | None:
    for key in ("primary_fact", canonical_ref):
        try:
            exact = _exact_payload(gateway.lookup_exact(namespace=project_id, project_id=project_id, key=key))
        except Exception:
            continue
        if exact.get("canonical_ref") == canonical_ref:
            return exact
    return None


def _find_exact_record(records: Mapping[str, Mapping[str, Any]], canonical_ref: str) -> Mapping[str, Any] | None:
    for record in records.values():
        if record.get("canonical_ref") == canonical_ref:
            return record
    return None


def _hash_matches(result: Mapping[str, Any], exact: Mapping[str, Any]) -> bool:
    candidates = {
        str(exact.get(name))
        for name in ("content_hash", "value_hash", "integrity_hash")
        if isinstance(exact.get(name), str)
    }
    for ref in exact.get("provenance_refs", []):
        if isinstance(ref, Mapping) and isinstance(ref.get("evidence_hash"), str):
            candidates.add(str(ref["evidence_hash"]))
    result_hashes = {
        str(result.get(name))
        for name in ("content_hash", "value_hash", "integrity_hash", "source_evidence_hash")
        if isinstance(result.get(name), str)
    }
    for ref in result.get("source_attribution", []):
        if isinstance(ref, Mapping):
            for name in ("content_hash", "value_hash", "evidence_hash"):
                if isinstance(ref.get(name), str):
                    result_hashes.add(str(ref[name]))
    return not candidates or bool(candidates & result_hashes)


def _provenance_matches(result: Mapping[str, Any], exact: Mapping[str, Any]) -> bool:
    exact_refs = {
        (str(ref.get("ref") or ref.get("canonical_ref")), str(ref.get("evidence_hash") or ref.get("value_hash")))
        for ref in exact.get("provenance_refs", [])
        if isinstance(ref, Mapping)
    }
    if not exact_refs:
        return True
    result_refs = set()
    for field in ("provenance_refs", "source_attribution"):
        for ref in result.get(field, []):
            if isinstance(ref, Mapping):
                result_refs.add(
                    (
                        str(ref.get("ref") or ref.get("canonical_ref")),
                        str(ref.get("evidence_hash") or ref.get("value_hash")),
                    )
                )
    return bool(exact_refs & result_refs)


def _result_count(layer: object) -> int:
    if not isinstance(layer, Mapping):
        return 0
    response = layer.get("response")
    if isinstance(response, Mapping) and isinstance(response.get("results"), list):
        return len(response["results"])
    return 0


def _layer_name(layer: object) -> str | None:
    if isinstance(layer, Mapping) and isinstance(layer.get("layer"), str):
        return str(layer["layer"])
    return None


def _nested_strings(value: object) -> tuple[str, ...]:
    strings: list[str] = []
    if isinstance(value, Mapping):
        for child in value.values():
            strings.extend(_nested_strings(child))
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            strings.extend(_nested_strings(child))
    elif isinstance(value, str):
        strings.append(value)
    return tuple(strings)


def _is_distinctive_private_string(text: str) -> bool:
    if len(text) < 9 or text.casefold() in _GENERIC_WORDS:
        return False
    if hashlib.sha256(text.encode("utf-8")).hexdigest() == text:
        return False
    alphabetic = [char for char in text.casefold() if char.isalpha()]
    if len(alphabetic) < 9:
        return False
    unique_ratio = len(set(alphabetic)) / len(alphabetic)
    words = [word.casefold() for word in re.findall(r"[A-Za-z]{3,}", text)]
    if len(words) == 1 and words[0] in _GENERIC_WORDS:
        return False
    return unique_ratio >= 0.32 or len(set(words) - _GENERIC_WORDS) >= 2


def _jsonable(value: object) -> object:
    try:
        json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return repr(value)
    return value


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("current_canonical_revision must be a non-negative integer")
    return value
