from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway
from core.memory_gateway_policy import MemoryGatewayPolicyError
from core.memory_scope_resolver import (
    MemoryScopeError,
    MemoryTransitionScope,
    resolve_memory_transition_scope,
    task_transition_hash,
)
from core.private_memory_history import canonical_json, content_hash


MEMORY_BOOTSTRAP_REQUEST_SCHEMA = "skeleton.memory_bootstrap.request.v1"
MEMORY_BOOTSTRAP_RESPONSE_SCHEMA = "skeleton.memory_bootstrap.response.v1"
MEMORY_BOOTSTRAP_RECEIPT_SCHEMA = "skeleton.memory_bootstrap.receipt.v1"
PRIVATE_CONTEXT_ENV = "SKELETON_PRIVATE_MEMORY_CONTEXT_FILE"
ECHO_SENTINEL_LABEL = "SKELETON_PRIVATE_ECHO_SENTINEL"
_DEFAULT_FACT_NAMESPACE = "skeleton.operator_preferences"
_DEFAULT_FACT_ID = "fast_autonomous_execution_v1"
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


class MemoryBootstrapError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, private_detail: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.private_detail = private_detail or message


class MemoryBootstrap:
    """Controlled entrypoint for private memory source selection.

    Canonical status/read/list calls go through the injected private MemoryGateway.
    Local-private details stay in the returned private context; public receipts are
    aggregate-only.
    """

    def __init__(
        self,
        gateway: MemoryGateway,
        *,
        cognee_adapter: object | None = None,
        mempalace_adapter: object | None = None,
        graphify_adapter: object | None = None,
        cache_dir: str | Path | None = None,
    ) -> None:
        self._gateway = gateway
        self._cognee = cognee_adapter
        self._mempalace = mempalace_adapter
        self._graphify = graphify_adapter
        self._cache_dir = Path(cache_dir or (Path(tempfile.gettempdir()) / "skeleton-memory-bootstrap-cache"))

    def bootstrap(self, request: Mapping[str, object]) -> dict[str, object]:
        if request.get("schema") != MEMORY_BOOTSTRAP_REQUEST_SCHEMA:
            raise MemoryBootstrapError("INVALID_BOOTSTRAP_REQUEST", "bootstrap request schema is invalid")
        if request.get("mode") not in {"mandatory", "private"}:
            raise MemoryBootstrapError("INVALID_BOOTSTRAP_MODE", "bootstrap mode is invalid")
        try:
            scope = resolve_memory_transition_scope(request)
        except MemoryScopeError as exc:
            raise MemoryBootstrapError(exc.reason_code, "memory scope is not authorized", private_detail=str(exc)) from exc
        status = self._gateway_payload("memory.private_status", {"project_id": scope.project_id})
        state = status.get("state")
        if state != "READY":
            raise MemoryBootstrapError("PRIVATE_MEMORY_NOT_READY", "private memory is not ready")
        revision_payload = self._gateway_payload("memory.private_current_revision", {"project_id": scope.project_id})
        current_revision = _positive_revision(revision_payload.get("canonical_revision"), allow_zero=True)
        cache_key = _cache_key(scope, current_revision)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached
        refs_payload = self._gateway_payload(
            "memory.private_list_exact",
            {
                "project_id": scope.project_id,
                "fact_namespace": request.get("fact_namespace", _DEFAULT_FACT_NAMESPACE),
                "limit": request.get("limit", 4),
            },
        )
        refs = refs_payload.get("result_refs")
        if not isinstance(refs, list) or not refs:
            raise MemoryBootstrapError("EXACT_CONTEXT_MISSING", "mandatory memory exact context is missing")
        records = []
        for canonical_ref in refs:
            namespace, fact_id = _split_canonical_ref(canonical_ref)
            exact = self._gateway_payload(
                "memory.private_read_exact",
                {
                    "project_id": scope.project_id,
                    "fact_namespace": namespace,
                    "fact_id": fact_id,
                },
            )
            records.append(_validated_exact_record(exact, scope, current_revision))
        context = _private_context(scope=scope, current_revision=current_revision, records=records)
        semantic = self._semantic_layers(scope, current_revision, records)
        result = {
            "schema": MEMORY_BOOTSTRAP_RESPONSE_SCHEMA,
            "status": "READY",
            "private_context": context,
            "semantic_layers": semantic,
            "public_receipt": _public_receipt(
                status="READY",
                reason_codes=("READY",),
                record_count=len(records),
                semantic_count=len(semantic),
            ),
        }
        self._write_cache(cache_key, result)
        return result

    def _gateway_payload(self, suffix: str, payload: Mapping[str, object]) -> dict[str, object]:
        try:
            response = self._gateway.execute(
                {
                    "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                    "namespace": "skeleton",
                    "command": f"skeleton.{suffix}",
                    "payload": dict(payload),
                }
            )
        except MemoryGatewayPolicyError as exc:
            raise MemoryBootstrapError(exc.reason_code, "private memory gateway rejected request", private_detail=str(exc)) from exc
        value = response.get("payload")
        if not isinstance(value, Mapping):
            raise MemoryBootstrapError("INVALID_GATEWAY_RESPONSE", "private memory gateway response is invalid")
        return dict(value)

    def _semantic_layers(
        self,
        scope: MemoryTransitionScope,
        current_revision: int,
        records: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        query = " ".join(str(record["canonical_ref"]) for record in records)[:512]
        layers: list[dict[str, object]] = []
        if self._cognee is not None:
            try:
                health = self._cognee.health(
                    project_id=scope.project_id,
                    dataset_id=scope.dataset_id,
                    current_canonical_revision=current_revision,
                )
                if health.get("status") == "READY":
                    recall = self._cognee.recall(
                        {
                            "schema": "skeleton.semantic_memory.recall_request.v1",
                            "project_id": scope.project_id,
                            "dataset_id": scope.dataset_id,
                            "query": query or "canonical",
                            "current_canonical_revision": current_revision,
                            "limit": 4,
                        }
                    )
                    layers.append({"kind": "cognee", "status": "READY", "aggregate_counts": _counts(recall)})
            except Exception:
                pass
        if not any(layer["kind"] == "cognee" for layer in layers) and self._mempalace is not None:
            try:
                result = self._mempalace.search(query=query or "canonical", limit=4)
                stale = any(item.get("stale") for item in result.get("results", []) if isinstance(item, Mapping))
                if not stale:
                    layers.append({"kind": "mempalace", "status": "READY", "aggregate_counts": _counts(result)})
            except Exception:
                pass
        if self._graphify is not None:
            try:
                result = self._graphify.query(query=query or "canonical", limit=4)
                stale = any(item.get("stale") for item in result.get("results", []) if isinstance(item, Mapping))
                if not stale:
                    layers.append({"kind": "graphify", "status": "READY", "aggregate_counts": _counts(result)})
            except Exception:
                pass
        return layers

    def _read_cache(self, cache_key: str) -> dict[str, object] | None:
        path = self._cache_dir / f"{cache_key}.json"
        lock = self._cache_dir / ".cache.lock"
        self._cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _locked(lock):
            if not path.is_file():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                path.unlink(missing_ok=True)
                return None
            if data.get("cache_key") != cache_key or data.get("response", {}).get("status") != "READY":
                return None
            response = data.get("response")
            return dict(response) if isinstance(response, Mapping) else None

    def _write_cache(self, cache_key: str, response: Mapping[str, object]) -> None:
        self._cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = self._cache_dir / ".cache.lock"
        path = self._cache_dir / f"{cache_key}.json"
        payload = {"cache_key": cache_key, "response": response}
        with _locked(lock):
            fd, tmp_name = tempfile.mkstemp(prefix=f".{cache_key}.", suffix=".tmp", dir=self._cache_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(canonical_json(payload))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, path)
                path.chmod(0o600)
            finally:
                Path(tmp_name).unlink(missing_ok=True)


@contextmanager
def private_context_file(context: Mapping[str, object]):
    fd, name = tempfile.mkstemp(prefix="skeleton-private-context-", suffix=".json")
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(canonical_json(context))
            handle.flush()
            os.fsync(handle.fileno())
        yield path
    finally:
        path.unlink(missing_ok=True)


def public_receipt_for_error(reason_code: str) -> dict[str, object]:
    return _public_receipt(status="BLOCKED", reason_codes=(_safe_reason(reason_code),), record_count=0, semantic_count=0)


def output_contains_private_echo(output: str, private_context: Mapping[str, object]) -> bool:
    if not output:
        return False
    sentinel = str(private_context.get("echo_sentinel", ""))
    marker = str(private_context.get("sentinel_marker", ""))
    if len(sentinel) >= 24 and sentinel in output:
        return True
    if len(marker) >= 24 and marker in output:
        return True
    for value in _specific_strings(private_context):
        if value in {sentinel, marker}:
            continue
        if len(value) >= 16 and value in output:
            return True
    return False


def _validated_exact_record(exact: Mapping[str, object], scope: MemoryTransitionScope, current_revision: int) -> dict[str, object]:
    if exact.get("project_id") != scope.project_id:
        raise MemoryBootstrapError("EXACT_SCOPE_MISMATCH", "canonical exact project mismatch")
    if exact.get("authoritative") is not True:
        raise MemoryBootstrapError("EXACT_NOT_AUTHORITATIVE", "canonical exact is not authoritative")
    if exact.get("authority_classification") not in {"canonical_exact", "canonical_sqlite"}:
        raise MemoryBootstrapError("EXACT_AUTHORITY_INVALID", "canonical exact authority is invalid")
    if exact.get("source_kind") != "canonical_sqlite":
        raise MemoryBootstrapError("EXACT_SOURCE_INVALID", "canonical exact source kind is invalid")
    revision = _positive_revision(exact.get("canonical_revision"), allow_zero=False)
    if revision > current_revision:
        raise MemoryBootstrapError("EXACT_REVISION_NEWER_THAN_CURRENT", "canonical exact revision is newer than current")
    value_hash = exact.get("value_hash")
    if not isinstance(value_hash, str) or _HASH_RE.fullmatch(value_hash) is None:
        raise MemoryBootstrapError("EXACT_VALUE_HASH_INVALID", "canonical exact value hash is invalid")
    value = exact.get("_private_value")
    if content_hash(value) != value_hash:
        raise MemoryBootstrapError("EXACT_VALUE_HASH_MISMATCH", "canonical exact value hash mismatch")
    canonical_ref = exact.get("canonical_ref")
    if not isinstance(canonical_ref, str) or ":" not in canonical_ref:
        raise MemoryBootstrapError("EXACT_CANONICAL_REF_INVALID", "canonical exact reference is invalid")
    provenance = exact.get("provenance_refs")
    if not isinstance(provenance, list) or not provenance:
        raise MemoryBootstrapError("EXACT_PROVENANCE_MISSING", "canonical exact provenance is missing")
    for ref in provenance:
        if not isinstance(ref, Mapping) or ref.get("kind") != "exact_source" or ref.get("evidence_hash") != value_hash:
            raise MemoryBootstrapError("EXACT_PROVENANCE_INVALID", "canonical exact provenance is invalid")
    return {
        "canonical_ref": canonical_ref,
        "canonical_revision": revision,
        "value_hash": value_hash,
        "value": value,
        "provenance_refs": provenance,
    }


def _private_context(*, scope: MemoryTransitionScope, current_revision: int, records: list[dict[str, object]]) -> dict[str, object]:
    sentinel = hashlib.sha256(
        f"{scope.cache_key}:{current_revision}:private-echo-sentinel".encode("utf-8")
    ).hexdigest()
    return {
        "schema": "skeleton.private_memory_context.v1",
        "project_id": scope.project_id,
        "dataset_id": scope.dataset_id,
        "canonical_revision": current_revision,
        "scope_cache_key": scope.cache_key,
        "sentinel_marker": ECHO_SENTINEL_LABEL,
        "echo_sentinel": sentinel,
        "records": records,
    }


def _public_receipt(*, status: str, reason_codes: tuple[str, ...], record_count: int, semantic_count: int) -> dict[str, object]:
    return {
        "schema": MEMORY_BOOTSTRAP_RECEIPT_SCHEMA,
        "status": status,
        "reason_codes": list(reason_codes),
        "aggregate_counts": {
            "record_count": record_count,
            "result_count": semantic_count,
        },
    }


def _cache_key(scope: MemoryTransitionScope, current_revision: int) -> str:
    return hashlib.sha256(f"{scope.cache_key}:{current_revision}".encode("ascii")).hexdigest()


def _positive_revision(value: object, *, allow_zero: bool) -> int:
    minimum = 0 if allow_zero else 1
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise MemoryBootstrapError("CANONICAL_REVISION_INVALID", "canonical revision is invalid")
    return value


def _split_canonical_ref(value: object) -> tuple[str, str]:
    if not isinstance(value, str) or ":" not in value:
        raise MemoryBootstrapError("CANONICAL_REF_INVALID", "canonical reference is invalid")
    namespace, fact_id = value.split(":", 1)
    if not namespace or not fact_id:
        raise MemoryBootstrapError("CANONICAL_REF_INVALID", "canonical reference is invalid")
    return namespace, fact_id


def _counts(result: Mapping[str, object]) -> dict[str, int]:
    results = result.get("results")
    if isinstance(results, list):
        return {"result_count": len(results)}
    counts = result.get("aggregate_counts")
    if isinstance(counts, Mapping):
        return {str(k): int(v) for k, v in counts.items() if isinstance(v, int) and not isinstance(v, bool) and v >= 0}
    return {"result_count": 0}


def _safe_reason(reason_code: str) -> str:
    return reason_code if re.fullmatch(r"[A-Z0-9_]{2,80}", reason_code) else "MEMORY_BOOTSTRAP_ERROR"


def _specific_strings(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for child in value.values():
            found.update(_specific_strings(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_specific_strings(child))
    elif isinstance(value, str) and len(value) >= 16 and re.search(r"[A-Za-z].*[A-Za-z].*[0-9]|[0-9].*[A-Za-z].*[A-Za-z]", value):
        found.add(value)
    return found


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def bootstrap_request_from_task(
    *,
    task_content: str,
    project_id: str,
    dataset_id: str,
    repository: str,
    branch: str,
) -> dict[str, object]:
    return {
        "schema": MEMORY_BOOTSTRAP_REQUEST_SCHEMA,
        "mode": "mandatory",
        "project_id": project_id,
        "dataset_id": dataset_id,
        "repository": repository,
        "branch": branch,
        "task_transition_hash": task_transition_hash(task_content),
        "fact_namespace": _DEFAULT_FACT_NAMESPACE,
        "fact_id": _DEFAULT_FACT_ID,
        "limit": 4,
    }
