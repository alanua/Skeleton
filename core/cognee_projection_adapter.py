from __future__ import annotations

import importlib.util
import sqlite3
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Protocol

from core.cognee_local_runtime import (
    COGNEE_PACKAGE_REQUIREMENT,
    PINNED_COGNEE_VERSION,
    CogneeLocalRuntimeError,
    CogneePackageFacade,
    CogneeRuntimePaths,
    ensure_private_runtime_tree,
    read_activation_marker,
    validate_local_provider_config,
)
from core.semantic_memory_projection import (
    COGNEE_DEPENDENCY_UNAVAILABLE,
    COGNEE_RUNTIME_NOT_IMPLEMENTED,
    CROSS_PROJECT_RECALL_FORBIDDEN,
    MEMORY_UNAVAILABLE,
    PROJECTION_STALE,
    SEMANTIC_HEALTH_SCHEMA,
    SEMANTIC_RECALL_RESPONSE_SCHEMA,
    SemanticProjectionError,
    SemanticProjectionEvent,
    SemanticProjectionHealth,
    SemanticRecallRequest,
    SemanticRecallResponse,
    SemanticRecallResult,
    SemanticScope,
    health_to_public_dict,
    public_receipt,
    recall_response_to_private_dict,
    receipt_to_public_dict,
    sanitize_projection_event,
    sanitize_recall_request,
    sanitize_scope,
    _strict_hash,
)


class CogneeBackend(Protocol):
    def project(self, event: SemanticProjectionEvent) -> None:
        ...

    def recall(self, request: SemanticRecallRequest) -> tuple[SemanticRecallResult, ...]:
        ...

    def health(self, scope: SemanticScope, *, current_canonical_revision: int) -> SemanticProjectionHealth:
        ...

    def forget_projection(self, scope: SemanticScope) -> int:
        ...


class DisposableInMemoryCogneeBackend:
    """Deterministic adapter-local backend used by tests and offline publication."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], dict[str, SemanticProjectionEvent]] = {}
        self.recall_calls = 0

    def project(self, event: SemanticProjectionEvent) -> None:
        key = _scope_key(event.scope)
        scoped = self._events.setdefault(key, {})
        scoped[event.canonical_ref] = event

    def recall(self, request: SemanticRecallRequest) -> tuple[SemanticRecallResult, ...]:
        self.recall_calls += 1
        key = _scope_key(request.scope)
        terms = set(request.query.casefold().split())
        scored: list[tuple[int, str, SemanticRecallResult]] = []
        for event in self._events.get(key, {}).values():
            haystack = event.bounded_text.casefold()
            score = sum(1 for term in terms if term in haystack)
            if score == 0 and terms:
                continue
            scored.append(
                (
                    -score,
                    event.canonical_ref,
                    SemanticRecallResult(
                        canonical_ref=event.canonical_ref,
                        canonical_revision=event.canonical_revision,
                        content_hash=event.content_hash,
                        projection_text_hash=event.projection_text_hash,
                        score=float(score),
                        metadata={
                            "project_id": event.scope.project_id,
                            "dataset_id": event.scope.dataset_id,
                            "synthetic": True,
                            "provenance_count": len(event.provenance),
                        },
                    ),
                )
            )
        return tuple(item for _, _, item in sorted(scored)[: request.limit])

    def health(self, scope: SemanticScope, *, current_canonical_revision: int) -> SemanticProjectionHealth:
        events = tuple(self._events.get(_scope_key(scope), {}).values())
        indexed_revision = max((event.canonical_revision for event in events), default=0)
        reason_codes = () if indexed_revision == current_canonical_revision else (PROJECTION_STALE,)
        return SemanticProjectionHealth(
            schema=SEMANTIC_HEALTH_SCHEMA,
            status="READY" if not reason_codes else "STALE",
            scope=scope,
            current_canonical_revision=current_canonical_revision,
            indexed_canonical_revision=indexed_revision,
            aggregate_counts={"event_count": len(events), "result_count": len(events)},
            reason_codes=reason_codes,
            authoritative=False,
        )

    def forget_projection(self, scope: SemanticScope) -> int:
        return len(self._events.pop(_scope_key(scope), {}))


class CogneePackageBackend:
    """Pinned Cognee package backend with a local private projection ledger."""

    def __init__(
        self,
        *,
        private_root: str | Path | None = None,
        runtime_enabled: bool | None = None,
        facade: CogneePackageFacade | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._paths: CogneeRuntimePaths | None = None
        self._facade = facade
        self._provider_valid = False
        self.dependency_available = facade is not None or importlib.util.find_spec("cognee") is not None
        self.runtime_enabled = bool(runtime_enabled)
        if private_root is not None:
            self._paths = ensure_private_runtime_tree(private_root)
            marker = read_activation_marker(private_root)
            self.runtime_enabled = bool(marker and marker.get("enabled")) if runtime_enabled is None else bool(runtime_enabled)
        try:
            validate_local_provider_config(env)
            self._provider_valid = True
        except CogneeLocalRuntimeError:
            self._provider_valid = False
        if self._facade is None and self.dependency_available and self.runtime_enabled and private_root is not None:
            try:
                self._facade = CogneePackageFacade()
            except (ImportError, CogneeLocalRuntimeError):
                self._facade = None

    @property
    def dependency_requirement(self) -> str:
        return COGNEE_PACKAGE_REQUIREMENT

    def _ready(self) -> CogneePackageFacade:
        if not self.dependency_available:
            raise SemanticProjectionError(COGNEE_DEPENDENCY_UNAVAILABLE, "optional cognee package is unavailable")
        if not self.runtime_enabled:
            raise SemanticProjectionError(MEMORY_UNAVAILABLE, "cognee runtime activation is not enabled")
        if self._facade is None:
            raise SemanticProjectionError(COGNEE_RUNTIME_NOT_IMPLEMENTED, "cognee package facade is unavailable")
        if self._paths is None:
            raise SemanticProjectionError("PRIVATE_RUNTIME_ROOT_REQUIRED", "private runtime root is required")
        if not self._provider_valid:
            raise SemanticProjectionError("LOCAL_PROVIDER_CONFIGURATION_INVALID", "local provider configuration is invalid")
        return self._facade

    def project(self, event: SemanticProjectionEvent) -> None:
        facade = self._ready()
        assert self._paths is not None
        _ensure_projection_schema(self._paths.projection_db)
        key = _projection_key(event)
        with sqlite3.connect(str(self._paths.projection_db)) as connection:
            existing = connection.execute(
                "SELECT content_hash, projection_text_hash FROM cognee_projection_events WHERE projection_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                if existing != (event.content_hash, event.projection_text_hash):
                    raise SemanticProjectionError("PROJECTION_IDEMPOTENCY_CONFLICT", "projection key conflicts with existing hashes")
                return
            facade.project(
                text=event.bounded_text,
                metadata={
                    "project_id": event.scope.project_id,
                    "dataset_id": event.scope.dataset_id,
                    "canonical_ref": event.canonical_ref,
                    "canonical_revision": event.canonical_revision,
                    "content_hash": event.content_hash,
                    "projection_text_hash": event.projection_text_hash,
                    "cognee_version": PINNED_COGNEE_VERSION,
                },
            )
            connection.execute(
                """
                INSERT INTO cognee_projection_events (
                    projection_key, project_id, dataset_id, canonical_ref, canonical_revision,
                    content_hash, projection_text_hash, bounded_text, provenance_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    event.scope.project_id,
                    event.scope.dataset_id,
                    event.canonical_ref,
                    event.canonical_revision,
                    event.content_hash,
                    event.projection_text_hash,
                    event.bounded_text,
                    _json(event.provenance),
                ),
            )
            connection.commit()
        self._paths.projection_db.chmod(0o600)

    def recall(self, request: SemanticRecallRequest) -> tuple[SemanticRecallResult, ...]:
        facade = self._ready()
        assert self._paths is not None
        if not facade.health():
            raise SemanticProjectionError(MEMORY_UNAVAILABLE, "cognee health check failed")
        _ensure_projection_schema(self._paths.projection_db)
        facade.recall(query=request.query, limit=request.limit)
        terms = set(request.query.casefold().split())
        with sqlite3.connect(str(self._paths.projection_db)) as connection:
            rows = connection.execute(
                """
                SELECT canonical_ref, canonical_revision, content_hash, projection_text_hash, bounded_text
                FROM cognee_projection_events
                WHERE project_id = ? AND dataset_id = ? AND canonical_revision <= ?
                """,
                (request.scope.project_id, request.scope.dataset_id, request.current_canonical_revision),
            ).fetchall()
        scored: list[tuple[int, str, SemanticRecallResult]] = []
        for ref, revision, content, text_hash, text in rows:
            haystack = str(text).casefold()
            score = sum(1 for term in terms if term in haystack) if terms else 0
            if score == 0 and terms:
                continue
            scored.append(
                (
                    -score,
                    str(ref),
                    SemanticRecallResult(
                        canonical_ref=str(ref),
                        canonical_revision=int(revision),
                        content_hash=str(content),
                        projection_text_hash=str(text_hash),
                        score=float(score),
                        metadata={
                            "project_id": request.scope.project_id,
                            "dataset_id": request.scope.dataset_id,
                            "source_kind": "cognee",
                            "cognee_version": PINNED_COGNEE_VERSION,
                        },
                    ),
                )
            )
        return tuple(item for _, _, item in sorted(scored)[: request.limit])

    def health(self, scope: SemanticScope, *, current_canonical_revision: int) -> SemanticProjectionHealth:
        if not self.dependency_available:
            return SemanticProjectionHealth(
                schema=SEMANTIC_HEALTH_SCHEMA,
                status="UNAVAILABLE",
                scope=scope,
                current_canonical_revision=current_canonical_revision,
                indexed_canonical_revision=0,
                aggregate_counts={"event_count": 0, "result_count": 0},
                reason_codes=(COGNEE_DEPENDENCY_UNAVAILABLE,),
                authoritative=False,
            )
        if not self.runtime_enabled:
            return SemanticProjectionHealth(
                schema=SEMANTIC_HEALTH_SCHEMA,
                status="UNAVAILABLE",
                scope=scope,
                current_canonical_revision=current_canonical_revision,
                indexed_canonical_revision=0,
                aggregate_counts={"event_count": 0, "result_count": 0},
                reason_codes=(MEMORY_UNAVAILABLE,),
                authoritative=False,
            )
        if self._paths is None or self._facade is None:
            return SemanticProjectionHealth(
                schema=SEMANTIC_HEALTH_SCHEMA,
                status="UNAVAILABLE",
                scope=scope,
                current_canonical_revision=current_canonical_revision,
                indexed_canonical_revision=0,
                aggregate_counts={"event_count": 0, "result_count": 0},
                reason_codes=(COGNEE_RUNTIME_NOT_IMPLEMENTED,),
                authoritative=False,
            )
        if not self._provider_valid:
            return SemanticProjectionHealth(
                schema=SEMANTIC_HEALTH_SCHEMA,
                status="UNAVAILABLE",
                scope=scope,
                current_canonical_revision=current_canonical_revision,
                indexed_canonical_revision=0,
                aggregate_counts={"event_count": 0, "result_count": 0},
                reason_codes=("LOCAL_PROVIDER_CONFIGURATION_INVALID",),
                authoritative=False,
            )
        _ensure_projection_schema(self._paths.projection_db)
        with sqlite3.connect(str(self._paths.projection_db)) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(canonical_revision), 0), COUNT(*)
                FROM cognee_projection_events
                WHERE project_id = ? AND dataset_id = ?
                """,
                (scope.project_id, scope.dataset_id),
            ).fetchone()
        indexed = int(row[0])
        count = int(row[1])
        reason_codes = () if indexed == current_canonical_revision else (PROJECTION_STALE,)
        return SemanticProjectionHealth(
            schema=SEMANTIC_HEALTH_SCHEMA,
            status="READY" if not reason_codes else "STALE",
            scope=scope,
            current_canonical_revision=current_canonical_revision,
            indexed_canonical_revision=indexed,
            aggregate_counts={"event_count": count, "result_count": count},
            reason_codes=reason_codes,
            authoritative=False,
        )

    def forget_projection(self, scope: SemanticScope) -> int:
        facade = self._ready()
        assert self._paths is not None
        _ensure_projection_schema(self._paths.projection_db)
        dataset_key = f"{scope.project_id}:{scope.dataset_id}"
        facade.forget(dataset_key=dataset_key)
        with sqlite3.connect(str(self._paths.projection_db)) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM cognee_projection_events WHERE project_id = ? AND dataset_id = ?",
                (scope.project_id, scope.dataset_id),
            ).fetchone()
            connection.execute(
                "DELETE FROM cognee_projection_events WHERE project_id = ? AND dataset_id = ?",
                (scope.project_id, scope.dataset_id),
            )
            connection.commit()
        self._paths.projection_db.chmod(0o600)
        return int(row[0])


class CogneeProjectionAdapter:
    """Derived, non-authoritative, project/dataset-scoped Cognee projection adapter."""

    def __init__(self, backend: CogneeBackend | None = None) -> None:
        self._backend = backend or CogneePackageBackend()

    def project(self, event: Mapping[str, object]) -> dict[str, object]:
        projection_event = sanitize_projection_event(event)
        self._backend.project(projection_event)
        receipt = public_receipt(
            status="PROJECTED",
            event_count=1,
            result_count=0,
            indexed_canonical_revision=projection_event.canonical_revision,
            current_canonical_revision=projection_event.canonical_revision,
            content_hashes=(projection_event.content_hash,),
            projection_text_hashes=(projection_event.projection_text_hash,),
            reason_codes=("PROJECTED",),
        )
        return receipt_to_public_dict(receipt)

    def recall(self, request: Mapping[str, object]) -> dict[str, object]:
        recall_request = sanitize_recall_request(request)
        health = self._backend.health(
            recall_request.scope,
            current_canonical_revision=recall_request.current_canonical_revision,
        )
        if health.indexed_canonical_revision != recall_request.current_canonical_revision:
            raise SemanticProjectionError(PROJECTION_STALE, "projection is stale")
        results = self._backend.recall(recall_request)
        verified = tuple(_verify_result_scope(result, recall_request) for result in results)
        response = SemanticRecallResponse(
            schema=SEMANTIC_RECALL_RESPONSE_SCHEMA,
            status="OK",
            scope=recall_request.scope,
            current_canonical_revision=recall_request.current_canonical_revision,
            indexed_canonical_revision=health.indexed_canonical_revision,
            authoritative=False,
            results=verified,
        )
        return recall_response_to_private_dict(response)

    def health(self, *, project_id: object, dataset_id: object, current_canonical_revision: int) -> dict[str, object]:
        scope = sanitize_scope(project_id, dataset_id)
        health = self._backend.health(scope, current_canonical_revision=current_canonical_revision)
        if health.scope != scope:
            raise SemanticProjectionError(CROSS_PROJECT_RECALL_FORBIDDEN, "backend health scope mismatch")
        return health_to_public_dict(health)

    def forget_projection(self, *, project_id: object, dataset_id: object) -> dict[str, object]:
        scope = sanitize_scope(project_id, dataset_id)
        removed = self._backend.forget_projection(scope)
        receipt = public_receipt(
            status="FORGOTTEN",
            event_count=removed,
            result_count=0,
            indexed_canonical_revision=0,
            current_canonical_revision=0,
            reason_codes=("ADAPTER_LOCAL_FORGET",),
        )
        return receipt_to_public_dict(receipt)


def _scope_key(scope: SemanticScope) -> tuple[str, str]:
    return (scope.project_id, scope.dataset_id)


def _verify_result_scope(result: SemanticRecallResult, request: SemanticRecallRequest) -> SemanticRecallResult:
    metadata = dict(deepcopy(result.metadata))
    if metadata.get("project_id") != request.scope.project_id or metadata.get("dataset_id") != request.scope.dataset_id:
        raise SemanticProjectionError(CROSS_PROJECT_RECALL_FORBIDDEN, "backend returned foreign scope")
    if isinstance(result.canonical_revision, bool) or not isinstance(result.canonical_revision, int):
        raise SemanticProjectionError("INVALID_CANONICAL_REVISION", "backend returned malformed canonical revision")
    if result.canonical_revision < 1:
        raise SemanticProjectionError("INVALID_CANONICAL_REVISION", "backend returned unbound canonical revision")
    if result.canonical_revision > request.current_canonical_revision:
        raise SemanticProjectionError(PROJECTION_STALE, "backend returned a future canonical revision")
    _strict_hash(result.content_hash, "content_hash")
    _strict_hash(result.projection_text_hash, "projection_text_hash")
    if result.content_hash == result.projection_text_hash:
        raise SemanticProjectionError("RESULT_HASH_BINDING_INVALID", "content hash and projection text hash are distinct bindings")
    return replace(result, metadata=metadata)


def _projection_key(event: SemanticProjectionEvent) -> str:
    return _strict_hash(
        __import__("hashlib").sha256(
            _json(
                {
                    "project_id": event.scope.project_id,
                    "dataset_id": event.scope.dataset_id,
                    "canonical_ref": event.canonical_ref,
                    "canonical_revision": event.canonical_revision,
                    "content_hash": event.content_hash,
                }
            ).encode("utf-8")
        ).hexdigest(),
        "projection_key",
    )


def _ensure_projection_schema(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with sqlite3.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cognee_projection_events (
                projection_key TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                canonical_ref TEXT NOT NULL,
                canonical_revision INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                projection_text_hash TEXT NOT NULL,
                bounded_text TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()
    path.chmod(0o600)


def _json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
