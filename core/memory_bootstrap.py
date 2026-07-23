from __future__ import annotations

import json
import os
import secrets
import stat
import tempfile
import fcntl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from core.cognee_projection_adapter import CogneeProjectionAdapter
from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_policy import MemoryGatewayPolicyError
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.memory_scope_resolver import ExactMemoryScope, MemoryScopeResolutionError, resolve_exact_memory_scope
from core.private_memory_history import content_hash
from core.private_memory_stack import PrivateMemoryStack
from core.semantic_memory_projection import SEMANTIC_RECALL_REQUEST_SCHEMA, SemanticProjectionError


MEMORY_BOOTSTRAP_REQUEST_SCHEMA = "skeleton.memory_bootstrap.request.v1"
MEMORY_BOOTSTRAP_RESPONSE_SCHEMA = "skeleton.memory_bootstrap.response.v1"
MEMORY_BOOTSTRAP_RECEIPT_SCHEMA = "skeleton.memory_bootstrap.receipt.v1"
PRIVATE_CONTEXT_ENV = "SKELETON_PRIVATE_CONTEXT_FILE"
PRIVATE_CONTEXT_MARKER = "SKELETON_PRIVATE_MEMORY_CONTEXT_V1"
GENERIC_INTERNAL_ERROR = "INTERNAL_ERROR"
ALLOWED_PUBLIC_REASONS = frozenset(
    {
        "DONE",
        "MEMORY_CONFIGURATION_REQUIRED",
        "EXACT_SCOPE_REQUIRED",
        "EXACT_SCOPE_INVALID",
        "PRIVATE_MEMORY_STORAGE_REQUIRED",
        "PRIVATE_MEMORY_NOT_READY",
        "PRIVATE_CONTEXT_EMPTY",
        "PRIVATE_CONTEXT_ECHO_BLOCKED",
        "EXECUTOR_FAILED",
        GENERIC_INTERNAL_ERROR,
    }
)
_ADAPTER_CACHE: dict[str, CogneeProjectionAdapter] = {}


class MemoryBootstrapError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = _public_reason(reason_code)


@dataclass(frozen=True)
class MemoryBootstrapConfig:
    private_root: Path
    scope: ExactMemoryScope
    canonical_refs: tuple[str, ...]
    query: str
    repository_root: Path
    worktree_root: Path


Executor = Callable[[list[str], str, Mapping[str, str]], tuple[int, str]]


class MemoryBootstrap:
    """Controlled five-layer private memory bootstrap for Runner handoff."""

    def __init__(
        self,
        config: MemoryBootstrapConfig,
        *,
        cognee_adapter_factory: Callable[[], CogneeProjectionAdapter] | None = None,
    ) -> None:
        self.config = config
        self.stack = PrivateMemoryStack(config.private_root)
        self.gateway = MemoryGateway(
            capability_token(namespaces=("skeleton",), public_mode=False),
            private_memory_storage=PrivateMemoryGatewayStorage(self.stack),
        )
        self._cognee_adapter_factory = cognee_adapter_factory or CogneeProjectionAdapter

    @classmethod
    def from_request(
        cls,
        request: Mapping[str, object],
        *,
        cognee_adapter_factory: Callable[[], CogneeProjectionAdapter] | None = None,
    ) -> "MemoryBootstrap":
        if request.get("schema") != MEMORY_BOOTSTRAP_REQUEST_SCHEMA:
            raise MemoryBootstrapError("MEMORY_CONFIGURATION_REQUIRED", "bootstrap request schema is invalid")
        if request.get("mandatory") is not True:
            raise MemoryBootstrapError("MEMORY_CONFIGURATION_REQUIRED", "private memory is mandatory")
        root = request.get("private_root")
        if not isinstance(root, str) or not root:
            raise MemoryBootstrapError("PRIVATE_MEMORY_STORAGE_REQUIRED", "private memory root is required")
        scope = resolve_exact_memory_scope(request.get("scope") if isinstance(request.get("scope"), Mapping) else {})
        refs = request.get("canonical_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
            raise MemoryBootstrapError("PRIVATE_CONTEXT_EMPTY", "at least one exact canonical ref is required")
        query = request.get("query")
        repository_root = Path(str(request.get("repository_root") or os.getcwd())).resolve()
        worktree_root = Path(str(request.get("worktree_root") or repository_root)).resolve()
        return cls(
            MemoryBootstrapConfig(
                private_root=Path(root).expanduser().resolve(),
                scope=scope,
                canonical_refs=tuple(refs),
                query=query if isinstance(query, str) and query.strip() else "summary",
                repository_root=repository_root,
                worktree_root=worktree_root,
            ),
            cognee_adapter_factory=cognee_adapter_factory,
        )

    def build_private_context(self) -> dict[str, object]:
        status = self._gateway("memory.private_status", {})
        current_revision = int(status["payload"]["canonical_sqlite"]["canonical_revision"])
        if status["payload"].get("state") not in {"READY", "STALE"} or current_revision < 1:
            raise MemoryBootstrapError("PRIVATE_MEMORY_NOT_READY", "private memory canonical authority is not ready")
        cached = self._read_context_cache(current_revision)
        if cached is not None:
            cached["echo_sentinel"] = secrets.token_urlsafe(32)
            return cached
        exact_records = [
            self._validate_exact_record(
                self._gateway("memory.private_read_exact", {"canonical_ref": canonical_ref})["payload"],
                current_revision=current_revision,
            )
            for canonical_ref in self.config.canonical_refs
        ]
        if not exact_records:
            raise MemoryBootstrapError("PRIVATE_CONTEXT_EMPTY", "exact private context is empty")
        semantic = self._selected_semantic(exact_records, current_revision)
        graph = self._selected_graph(current_revision)
        context = {
            "schema": MEMORY_BOOTSTRAP_RESPONSE_SCHEMA,
            "marker": PRIVATE_CONTEXT_MARKER,
            "echo_sentinel": secrets.token_urlsafe(32),
            "scope": {
                "project_id": self.config.scope.project_id,
                "dataset_id": self.config.scope.dataset_id,
            },
            "canonical_revision": current_revision,
            "canonical": exact_records,
            "semantic": semantic,
            "graph": graph,
        }
        self._write_context_cache(context, current_revision)
        return context

    def execute(self, *, task_body: str, executor: Executor) -> dict[str, object]:
        argv = ["codex", "exec", "--sandbox", "workspace-write", "--cd", str(self.config.worktree_root)]
        private_file: Path | None = None
        context: dict[str, object] = {}
        try:
            context = self.build_private_context()
            private_file = _write_private_context_file(context, self.config)
            env = {PRIVATE_CONTEXT_ENV: str(private_file)}
            code, output = executor(argv, task_body, env)
            if _output_echoes_private(output, context):
                return _receipt("BLOCKED", "PRIVATE_CONTEXT_ECHO_BLOCKED", context)
            if code != 0:
                return _receipt("BLOCKED", "EXECUTOR_FAILED", context)
            receipt = _receipt("DONE", "DONE", context)
            receipt["safe_output"] = output
            return receipt
        except MemoryBootstrapError as exc:
            return _receipt("BLOCKED", exc.reason_code, {})
        except Exception:
            return _receipt("BLOCKED", GENERIC_INTERNAL_ERROR, {})
        finally:
            if private_file is not None:
                private_file.unlink(missing_ok=True)

    def _read_context_cache(self, current_revision: int) -> dict[str, object] | None:
        path = self._context_cache_path(current_revision)
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_fd = _open_private_lock(lock_path)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if not path.is_file():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                return None
            if not isinstance(payload, dict) or payload.get("cache_key") != self._context_cache_key(current_revision):
                return None
            context = payload.get("context")
            if not isinstance(context, dict):
                return None
            context = dict(context)
            if context.get("canonical_revision") != current_revision:
                return None
            return context
        finally:
            os.close(lock_fd)

    def _write_context_cache(self, context: Mapping[str, object], current_revision: int) -> None:
        path = self._context_cache_path(current_revision)
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_fd = _open_private_lock(lock_path)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            cached_context = dict(context)
            cached_context.pop("echo_sentinel", None)
            payload = {
                "cache_key": self._context_cache_key(current_revision),
                "context": cached_context,
            }
            fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
            tmp_path = Path(name)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
                os.close(fd)
                fd = -1
                os.replace(tmp_path, path)
                path.chmod(0o600)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
            finally:
                if fd >= 0:
                    os.close(fd)
        finally:
            os.close(lock_fd)

    def _context_cache_path(self, current_revision: int) -> Path:
        cache_dir = self.config.private_root / "bootstrap_context_cache"
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return cache_dir / f"{self._context_cache_key(current_revision)}.json"

    def _context_cache_key(self, current_revision: int) -> str:
        material = {
            "private_root": str(self.config.private_root),
            "scope": {
                "project_id": self.config.scope.project_id,
                "dataset_id": self.config.scope.dataset_id,
                "repository": self.config.scope.repository,
                "branch": self.config.scope.branch,
                "task_transition_hash": self.config.scope.task_transition_hash,
            },
            "canonical_refs": self.config.canonical_refs,
            "query": self.config.query,
            "canonical_revision": current_revision,
        }
        return content_hash(material)

    def _gateway(self, suffix: str, payload: Mapping[str, object]) -> dict[str, object]:
        try:
            return self.gateway.execute(
                {
                    "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                    "namespace": "skeleton",
                    "command": f"skeleton.{suffix}",
                    "payload": {
                        "project_id": self.config.scope.project_id,
                        "dataset_id": self.config.scope.dataset_id,
                        **dict(payload),
                    },
                }
            )
        except MemoryGatewayPolicyError as exc:
            raise MemoryBootstrapError(exc.reason_code, str(exc)) from exc

    def _validate_exact_record(self, record: Mapping[str, object], *, current_revision: int) -> dict[str, object]:
        if record.get("project_id") != self.config.scope.project_id or record.get("dataset_id") != self.config.scope.dataset_id:
            raise MemoryBootstrapError("EXACT_SCOPE_INVALID", "exact record scope mismatch")
        if record.get("authoritative") is not True or record.get("source_kind") != "canonical_sqlite":
            raise MemoryBootstrapError("PRIVATE_MEMORY_NOT_READY", "exact record is not canonical")
        revision = record.get("canonical_revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1 or revision > current_revision:
            raise MemoryBootstrapError("PRIVATE_MEMORY_NOT_READY", "exact record revision is invalid")
        if content_hash(record.get("value")) != record.get("value_hash"):
            raise MemoryBootstrapError("PRIVATE_MEMORY_NOT_READY", "exact record hash mismatch")
        if not isinstance(record.get("canonical_ref"), str) or ":" not in str(record.get("canonical_ref")):
            raise MemoryBootstrapError("PRIVATE_MEMORY_NOT_READY", "exact record ref is invalid")
        provenance = record.get("provenance_refs")
        if not isinstance(provenance, list) or not provenance:
            raise MemoryBootstrapError("PRIVATE_MEMORY_NOT_READY", "exact provenance is required")
        for item in provenance:
            if not _provenance_matches_exact(item, record):
                raise MemoryBootstrapError("PRIVATE_MEMORY_NOT_READY", "exact provenance is invalid")
        return dict(record)

    def _selected_semantic(self, exact_records: list[dict[str, object]], current_revision: int) -> dict[str, object]:
        adapter_key = f"{self.config.private_root}:{self.config.scope.cache_key}:{current_revision}"
        adapter = _ADAPTER_CACHE.get(adapter_key)
        if adapter is None:
            adapter = self._cognee_adapter_factory()
            _ADAPTER_CACHE[adapter_key] = adapter
        try:
            health = adapter.health(
                project_id=self.config.scope.project_id,
                dataset_id=self.config.scope.dataset_id,
                current_canonical_revision=current_revision,
            )
            if (
                self._valid_cognee_health(health, current_revision)
            ):
                recall = adapter.recall(
                    {
                        "schema": SEMANTIC_RECALL_REQUEST_SCHEMA,
                        "project_id": self.config.scope.project_id,
                        "dataset_id": self.config.scope.dataset_id,
                        "query": self.config.query,
                        "current_canonical_revision": current_revision,
                        "limit": 5,
                    }
                )
                if not self._valid_cognee_recall_envelope(recall, current_revision):
                    raise SemanticProjectionError("INVALID_RECALL_ENVELOPE", "cognee recall envelope is invalid")
                results = self._confirmed_projection_results(recall.get("results", []), exact_records, current_revision)
                if results:
                    return {"selected": "cognee", "results": results}
        except SemanticProjectionError:
            pass
        fallback = self._gateway("memory.private_search_semantic", {"query": self.config.query})["payload"]
        results = self._confirmed_projection_results(fallback.get("results", []), exact_records, current_revision)
        return {"selected": "mempalace" if results else "none", "results": results}

    def _selected_graph(self, current_revision: int) -> dict[str, object]:
        payload = self._gateway("graph.private_query", {"query": self.config.query})["payload"]
        results = payload.get("results") if payload.get("state") == "READY" else []
        confirmed = self._confirmed_graph_results(results, current_revision)
        return {"selected": "graphify" if confirmed else "none", "results": confirmed}

    def _valid_cognee_health(self, health: Mapping[str, object], current_revision: int) -> bool:
        revisions = health.get("canonical_revisions")
        return (
            health.get("status") == "READY"
            and isinstance(revisions, Mapping)
            and revisions.get("indexed_canonical_revision") == current_revision
            and revisions.get("current_canonical_revision") == current_revision
            and health.get("authoritative") is False
        )

    def _valid_cognee_recall_envelope(self, recall: Mapping[str, object], current_revision: int) -> bool:
        return (
            recall.get("project_id") == self.config.scope.project_id
            and recall.get("dataset_id") == self.config.scope.dataset_id
            and recall.get("current_canonical_revision") == current_revision
            and recall.get("indexed_canonical_revision") == current_revision
            and recall.get("authoritative") is False
        )

    def _confirmed_projection_results(
        self,
        results: object,
        exact_records: list[dict[str, object]],
        current_revision: int,
    ) -> list[dict[str, object]]:
        by_ref = {str(record["canonical_ref"]): record for record in exact_records}
        confirmed = []
        for result in results if isinstance(results, list) else []:
            if not isinstance(result, Mapping):
                continue
            canonical_ref = result.get("canonical_ref")
            exact = by_ref.get(str(canonical_ref))
            if exact is None:
                continue
            exact_read = self._validate_exact_record(
                self._gateway("memory.private_read_exact", {"canonical_ref": str(canonical_ref)})["payload"],
                current_revision=current_revision,
            )
            if exact_read.get("value_hash") != exact.get("value_hash"):
                continue
            result_revision = result.get("canonical_revision")
            if not isinstance(result_revision, int) or isinstance(result_revision, bool):
                continue
            if result_revision != exact_read.get("canonical_revision"):
                continue
            if result_revision > current_revision:
                continue
            result_hash = result.get("content_hash") or _source_value_hash(result)
            if result_hash != exact_read.get("value_hash"):
                continue
            provenance = _derived_result_provenance(result)
            if not _all_derived_provenance_matches(provenance, exact_read):
                continue
            confirmed.append(dict(result))
        return confirmed

    def _confirmed_graph_results(self, results: object, current_revision: int) -> list[dict[str, object]]:
        confirmed = []
        for result in results if isinstance(results, list) else []:
            if not isinstance(result, Mapping):
                continue
            canonical_ref = result.get("canonical_ref")
            if not isinstance(canonical_ref, str):
                continue
            exact = self._validate_exact_record(
                self._gateway("memory.private_read_exact", {"canonical_ref": canonical_ref})["payload"],
                current_revision=current_revision,
            )
            result_revision = result.get("canonical_revision")
            if not isinstance(result_revision, int) or isinstance(result_revision, bool):
                continue
            if result_revision != exact.get("canonical_revision"):
                continue
            result_hash = result.get("content_hash") or result.get("value_hash") or _source_value_hash(result)
            if result_hash != exact.get("value_hash"):
                continue
            provenance = _derived_result_provenance(result)
            if not _all_derived_provenance_matches(provenance, exact):
                continue
            confirmed.append(dict(result))
        return confirmed


def reset_bootstrap_adapter_cache() -> None:
    _ADAPTER_CACHE.clear()


def _source_value_hash(result: Mapping[str, object]) -> object:
    attribution = result.get("source_attribution")
    if isinstance(attribution, list) and attribution and isinstance(attribution[0], Mapping):
        return attribution[0].get("value_hash")
    return None


def _write_private_context_file(context: Mapping[str, object], config: MemoryBootstrapConfig) -> Path:
    fd, name = tempfile.mkstemp(prefix="skeleton-private-context-", suffix=".json")
    path = Path(name).resolve()
    try:
        _assert_outside(path, config.repository_root)
        _assert_outside(path, config.worktree_root)
        os.write(fd, json.dumps(context, sort_keys=True).encode("utf-8"))
        os.fchmod(fd, 0o600)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        path.chmod(0o600)
    return path


def _assert_outside(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        return
    raise MemoryBootstrapError("PRIVATE_MEMORY_STORAGE_REQUIRED", "private context file must be outside repository")


def _output_echoes_private(output: str, context: Mapping[str, object]) -> bool:
    if not output:
        return False
    needles = {PRIVATE_CONTEXT_MARKER, str(context.get("echo_sentinel") or "")}
    canonical = context.get("canonical")
    if isinstance(canonical, list):
        for record in canonical:
            if isinstance(record, Mapping):
                _collect_distinctive_strings(record.get("value"), needles)
    for section_name in ("semantic", "graph"):
        section = context.get(section_name)
        if not isinstance(section, Mapping):
            continue
        results = section.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if isinstance(result, Mapping):
                _collect_result_private_strings(result, needles)
    return any(needle and needle in output for needle in needles if needle != "summary")


def _collect_result_private_strings(result: Mapping[str, object], needles: set[str]) -> None:
    for key in ("bounded_text", "text", "value", "content", "summary", "result_text"):
        if key in result:
            _collect_distinctive_strings(result[key], needles)


def _collect_distinctive_strings(value: object, needles: set[str]) -> None:
    if isinstance(value, Mapping):
        for child in value.values():
            _collect_distinctive_strings(child, needles)
    elif isinstance(value, list):
        for child in value:
            _collect_distinctive_strings(child, needles)
    elif isinstance(value, str):
        text = value.strip()
        if len(text) >= 8 or (text.isalpha() and len(text) >= 5):
            needles.add(text)


def _open_private_lock(path: Path) -> int:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(fd, 0o600)
    return fd


def _provenance_matches_exact(item: object, exact: Mapping[str, object]) -> bool:
    if not isinstance(item, Mapping):
        return False
    ref = item.get("ref", item.get("canonical_ref"))
    evidence_hash = item.get("evidence_hash", item.get("value_hash"))
    kind = item.get("kind", "exact_source" if item.get("source_kind") == "canonical_sqlite" else None)
    return ref == exact.get("canonical_ref") and kind == "exact_source" and evidence_hash == exact.get("value_hash")


def _derived_result_provenance(result: Mapping[str, object]) -> object:
    for key in ("provenance", "provenance_refs", "source_attribution"):
        if key in result:
            return result[key]
    return None


def _derived_provenance_matches_exact(item: object, exact: Mapping[str, object]) -> bool:
    if not isinstance(item, Mapping):
        return False
    ref = item.get("canonical_ref", item.get("ref"))
    revision = item.get("canonical_revision")
    evidence_hash = item.get("value_hash", item.get("content_hash", item.get("evidence_hash")))
    source_kind = item.get("source_kind")
    if source_kind is None and item.get("kind") == "exact_source":
        source_kind = exact.get("source_kind")
    return (
        ref == exact.get("canonical_ref")
        and revision == exact.get("canonical_revision")
        and evidence_hash == exact.get("value_hash")
        and source_kind == exact.get("source_kind")
    )


def _all_derived_provenance_matches(value: object, exact: Mapping[str, object]) -> bool:
    if not isinstance(value, list) or not value:
        return False
    return all(_derived_provenance_matches_exact(item, exact) for item in value)


def _receipt(status: str, reason: str, context: Mapping[str, object]) -> dict[str, object]:
    counts = {
        "canonical_count": len(context.get("canonical", [])) if isinstance(context, Mapping) else 0,
        "semantic_count": len(context.get("semantic", {}).get("results", []))
        if isinstance(context.get("semantic"), Mapping)
        else 0,
        "graph_count": len(context.get("graph", {}).get("results", [])) if isinstance(context.get("graph"), Mapping) else 0,
    }
    return {
        "schema": MEMORY_BOOTSTRAP_RECEIPT_SCHEMA,
        "status": status,
        "reason_codes": [_public_reason(reason)],
        "aggregate_counts": counts,
    }


def _public_reason(reason: str) -> str:
    return reason if reason in ALLOWED_PUBLIC_REASONS else GENERIC_INTERNAL_ERROR
