from __future__ import annotations

import importlib.util
from copy import deepcopy
from dataclasses import replace
from typing import Mapping, Protocol

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
    """Runtime-gated boundary for the optional Cognee package.

    Offline source publication only checks whether the package is discoverable. Real
    model-backed indexing is intentionally deferred to a later runtime gate.
    """

    def __init__(self, *, runtime_enabled: bool = False) -> None:
        self.runtime_enabled = runtime_enabled
        self.dependency_available = importlib.util.find_spec("cognee") is not None

    def _blocked(self) -> None:
        if not self.dependency_available:
            raise SemanticProjectionError(COGNEE_DEPENDENCY_UNAVAILABLE, "optional cognee package is unavailable")
        if not self.runtime_enabled:
            raise SemanticProjectionError(MEMORY_UNAVAILABLE, "cognee runtime activation is not enabled")
        raise SemanticProjectionError(
            COGNEE_RUNTIME_NOT_IMPLEMENTED,
            "cognee package execution backend is not implemented",
        )

    def project(self, event: SemanticProjectionEvent) -> None:
        self._blocked()

    def recall(self, request: SemanticRecallRequest) -> tuple[SemanticRecallResult, ...]:
        self._blocked()
        return ()

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

    def forget_projection(self, scope: SemanticScope) -> int:
        self._blocked()
        return 0


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
