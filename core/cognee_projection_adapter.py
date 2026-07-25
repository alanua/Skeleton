from __future__ import annotations

import importlib.util
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Protocol

from core.cognee_local_runtime import (
    COGNEE_PACKAGE_REQUIREMENT,
    PINNED_COGNEE_VERSION,
    CogneeLocalRuntimeError,
    CogneeWorkerClient,
    cognee_runtime_paths,
    opaque_dataset_name,
    opaque_scope_hash,
    projection_document,
    read_activation_marker,
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
    def project(self, event: SemanticProjectionEvent) -> None: ...
    def recall(self, request: SemanticRecallRequest) -> tuple[SemanticRecallResult, ...]: ...
    def health(
        self, scope: SemanticScope, *, current_canonical_revision: int
    ) -> SemanticProjectionHealth: ...
    def forget_projection(self, scope: SemanticScope) -> int: ...


class DisposableInMemoryCogneeBackend:
    """Deterministic adapter-local backend used only by tests/offline publication."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], dict[str, SemanticProjectionEvent]] = {}
        self.recall_calls = 0

    def project(self, event: SemanticProjectionEvent) -> None:
        self._events.setdefault(_scope_key(event.scope), {})[event.canonical_ref] = event

    def recall(self, request: SemanticRecallRequest) -> tuple[SemanticRecallResult, ...]:
        self.recall_calls += 1
        terms = set(request.query.casefold().split())
        scored: list[tuple[int, str, SemanticRecallResult]] = []
        for event in self._events.get(_scope_key(request.scope), {}).values():
            haystack = event.bounded_text.casefold()
            score = sum(1 for term in terms if term in haystack)
            if score == 0 and terms:
                continue
            provenance = (
                {
                    "canonical_ref": event.canonical_ref,
                    "canonical_revision": event.canonical_revision,
                    "value_hash": event.content_hash,
                    "content_hash": event.content_hash,
                    "source_kind": "canonical_sqlite",
                },
            )
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
                        },
                        provenance=provenance,
                    ),
                )
            )
        return tuple(item for _, _, item in sorted(scored)[: request.limit])

    def health(
        self, scope: SemanticScope, *, current_canonical_revision: int
    ) -> SemanticProjectionHealth:
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
    """Pinned, isolated, local-only Cognee production backend."""

    def __init__(
        self,
        *,
        private_root: str | Path | None = None,
        runtime_enabled: bool | None = None,
        env: Mapping[str, str] | None = None,
        client: CogneeWorkerClient | None = None,
    ) -> None:
        configured_root = private_root or os.environ.get("SKELETON_RUNNER_PRIVATE_MEMORY_ROOT")
        self._private_root = Path(configured_root).expanduser().resolve() if configured_root else None
        self._client = client
        self._env = dict(os.environ if env is None else env)
        marker = read_activation_marker(self._private_root) if self._private_root is not None else None
        self.runtime_enabled = (
            bool(runtime_enabled)
            if runtime_enabled is not None
            else bool(marker and marker.get("enabled") is True)
        )
        python_exists = (
            self._private_root is not None
            and (cognee_runtime_paths(self._private_root).venv_dir / "bin" / "python").is_file()
        )
        package_discoverable = importlib.util.find_spec("cognee") is not None
        self.dependency_available = client is not None or python_exists or package_discoverable
        if self._client is None and self.dependency_available and self._private_root is not None:
            try:
                self._client = CogneeWorkerClient(self._private_root, env=self._env)
            except CogneeLocalRuntimeError:
                self._client = None

    @property
    def dependency_requirement(self) -> str:
        return COGNEE_PACKAGE_REQUIREMENT

    def _ready(self) -> CogneeWorkerClient:
        if not self.dependency_available:
            raise SemanticProjectionError(
                COGNEE_DEPENDENCY_UNAVAILABLE, "private Cognee dependency is unavailable"
            )
        if not self.runtime_enabled:
            raise SemanticProjectionError(
                MEMORY_UNAVAILABLE, "Cognee runtime activation is not enabled"
            )
        if self._client is None:
            raise SemanticProjectionError(
                COGNEE_RUNTIME_NOT_IMPLEMENTED, "isolated Cognee worker is unavailable"
            )
        if self._private_root is None:
            raise SemanticProjectionError(
                "PRIVATE_RUNTIME_ROOT_REQUIRED", "private runtime root is required"
            )
        return self._client

    def project(self, event: SemanticProjectionEvent) -> None:
        client = self._ready()
        dataset = opaque_dataset_name(event.scope.project_id, event.scope.dataset_id)
        document = projection_document(
            project_id=event.scope.project_id,
            dataset_id=event.scope.dataset_id,
            canonical_ref=event.canonical_ref,
            canonical_revision=event.canonical_revision,
            content_hash=event.content_hash,
            projection_text_hash=event.projection_text_hash,
            bounded_text=event.bounded_text,
        )
        try:
            client.project(dataset_name=dataset, document=document)
        except CogneeLocalRuntimeError as exc:
            raise SemanticProjectionError(exc.reason_code.upper(), str(exc)) from exc

    def recall(self, request: SemanticRecallRequest) -> tuple[SemanticRecallResult, ...]:
        client = self._ready()
        dataset = opaque_dataset_name(request.scope.project_id, request.scope.dataset_id)
        scope_hash = opaque_scope_hash(request.scope.project_id, request.scope.dataset_id)
        try:
            candidates = client.recall(
                dataset_name=dataset,
                opaque_scope_hash=scope_hash,
                query=request.query,
                current_canonical_revision=request.current_canonical_revision,
                limit=request.limit,
            )
        except CogneeLocalRuntimeError as exc:
            raise SemanticProjectionError(exc.reason_code.upper(), str(exc)) from exc
        results: list[SemanticRecallResult] = []
        for candidate in candidates:
            provenance = candidate.get("provenance")
            if not isinstance(provenance, list):
                raise SemanticProjectionError("INVALID_PROVENANCE", "Cognee provenance is invalid")
            results.append(
                SemanticRecallResult(
                    canonical_ref=str(candidate.get("canonical_ref", "")),
                    canonical_revision=candidate.get("canonical_revision"),  # type: ignore[arg-type]
                    content_hash=str(candidate.get("content_hash", "")),
                    projection_text_hash=str(candidate.get("projection_text_hash", "")),
                    score=float(candidate.get("score", 0.0)),
                    metadata={
                        "project_id": request.scope.project_id,
                        "dataset_id": request.scope.dataset_id,
                        "source_kind": "cognee",
                        "cognee_version": PINNED_COGNEE_VERSION,
                    },
                    provenance=tuple(dict(item) for item in provenance if isinstance(item, Mapping)),
                )
            )
        return tuple(results)

    def health(
        self, scope: SemanticScope, *, current_canonical_revision: int
    ) -> SemanticProjectionHealth:
        if not self.dependency_available:
            return _health(
                scope,
                current_canonical_revision,
                indexed=0,
                status="UNAVAILABLE",
                reason_codes=(COGNEE_DEPENDENCY_UNAVAILABLE,),
            )
        if not self.runtime_enabled:
            return _health(
                scope,
                current_canonical_revision,
                indexed=0,
                status="UNAVAILABLE",
                reason_codes=(MEMORY_UNAVAILABLE,),
            )
        if self._client is None:
            return _health(
                scope,
                current_canonical_revision,
                indexed=0,
                status="UNAVAILABLE",
                reason_codes=(COGNEE_RUNTIME_NOT_IMPLEMENTED,),
            )
        dataset = opaque_dataset_name(scope.project_id, scope.dataset_id)
        try:
            result = self._client.health(
                dataset_name=dataset,
                current_canonical_revision=current_canonical_revision,
            )
        except CogneeLocalRuntimeError as exc:
            return _health(
                scope,
                current_canonical_revision,
                indexed=0,
                status="UNAVAILABLE",
                reason_codes=(exc.reason_code.upper(),),
            )
        indexed = int(result.get("indexed_canonical_revision", 0))
        count = int(result.get("event_count", 0))
        if result.get("ready") is True and indexed == current_canonical_revision:
            return SemanticProjectionHealth(
                schema=SEMANTIC_HEALTH_SCHEMA,
                status="READY",
                scope=scope,
                current_canonical_revision=current_canonical_revision,
                indexed_canonical_revision=indexed,
                aggregate_counts={"event_count": count, "result_count": count},
                reason_codes=(),
                authoritative=False,
            )
        return SemanticProjectionHealth(
            schema=SEMANTIC_HEALTH_SCHEMA,
            status="STALE" if indexed != current_canonical_revision else "UNAVAILABLE",
            scope=scope,
            current_canonical_revision=current_canonical_revision,
            indexed_canonical_revision=indexed,
            aggregate_counts={"event_count": count, "result_count": count},
            reason_codes=(PROJECTION_STALE,) if indexed != current_canonical_revision else ("COGNEE_HEALTH_FAILED",),
            authoritative=False,
        )

    def forget_projection(self, scope: SemanticScope) -> int:
        client = self._ready()
        dataset = opaque_dataset_name(scope.project_id, scope.dataset_id)
        try:
            return client.forget(dataset_name=dataset)
        except CogneeLocalRuntimeError as exc:
            raise SemanticProjectionError(exc.reason_code.upper(), str(exc)) from exc


class CogneeProjectionAdapter:
    def __init__(self, backend: CogneeBackend | None = None) -> None:
        self._backend = backend or CogneePackageBackend()

    def project(self, event: Mapping[str, object]) -> dict[str, object]:
        projection_event = sanitize_projection_event(event)
        self._backend.project(projection_event)
        return receipt_to_public_dict(
            public_receipt(
                status="PROJECTED",
                event_count=1,
                result_count=0,
                indexed_canonical_revision=projection_event.canonical_revision,
                current_canonical_revision=projection_event.canonical_revision,
                content_hashes=(projection_event.content_hash,),
                projection_text_hashes=(projection_event.projection_text_hash,),
                reason_codes=("PROJECTED",),
            )
        )

    def recall(self, request: Mapping[str, object]) -> dict[str, object]:
        recall_request = sanitize_recall_request(request)
        health = self._backend.health(
            recall_request.scope,
            current_canonical_revision=recall_request.current_canonical_revision,
        )
        if (
            health.status != "READY"
            or health.indexed_canonical_revision != recall_request.current_canonical_revision
        ):
            raise SemanticProjectionError(PROJECTION_STALE, "projection is stale")
        results = self._backend.recall(recall_request)
        verified = tuple(_verify_result_scope(result, recall_request) for result in results)
        return recall_response_to_private_dict(
            SemanticRecallResponse(
                schema=SEMANTIC_RECALL_RESPONSE_SCHEMA,
                status="OK",
                scope=recall_request.scope,
                current_canonical_revision=recall_request.current_canonical_revision,
                indexed_canonical_revision=health.indexed_canonical_revision,
                authoritative=False,
                results=verified,
            )
        )

    def health(
        self, *, project_id: object, dataset_id: object, current_canonical_revision: int
    ) -> dict[str, object]:
        scope = sanitize_scope(project_id, dataset_id)
        health = self._backend.health(
            scope, current_canonical_revision=current_canonical_revision
        )
        if health.scope != scope:
            raise SemanticProjectionError(
                CROSS_PROJECT_RECALL_FORBIDDEN, "backend health scope mismatch"
            )
        return health_to_public_dict(health)

    def forget_projection(
        self, *, project_id: object, dataset_id: object
    ) -> dict[str, object]:
        scope = sanitize_scope(project_id, dataset_id)
        removed = self._backend.forget_projection(scope)
        return receipt_to_public_dict(
            public_receipt(
                status="FORGOTTEN",
                event_count=removed,
                result_count=0,
                indexed_canonical_revision=0,
                current_canonical_revision=0,
                reason_codes=("ADAPTER_LOCAL_FORGET",),
            )
        )


def _scope_key(scope: SemanticScope) -> tuple[str, str]:
    return (scope.project_id, scope.dataset_id)


def _health(
    scope: SemanticScope,
    current_revision: int,
    *,
    indexed: int,
    status: str,
    reason_codes: tuple[str, ...],
) -> SemanticProjectionHealth:
    return SemanticProjectionHealth(
        schema=SEMANTIC_HEALTH_SCHEMA,
        status=status,
        scope=scope,
        current_canonical_revision=current_revision,
        indexed_canonical_revision=indexed,
        aggregate_counts={"event_count": 0, "result_count": 0},
        reason_codes=reason_codes,
        authoritative=False,
    )


def _verify_result_scope(
    result: SemanticRecallResult, request: SemanticRecallRequest
) -> SemanticRecallResult:
    metadata = dict(deepcopy(result.metadata))
    if (
        metadata.get("project_id") != request.scope.project_id
        or metadata.get("dataset_id") != request.scope.dataset_id
    ):
        raise SemanticProjectionError(
            CROSS_PROJECT_RECALL_FORBIDDEN, "backend returned foreign scope"
        )
    if isinstance(result.canonical_revision, bool) or not isinstance(
        result.canonical_revision, int
    ):
        raise SemanticProjectionError(
            "INVALID_CANONICAL_REVISION", "backend returned malformed canonical revision"
        )
    if result.canonical_revision < 1:
        raise SemanticProjectionError(
            "INVALID_CANONICAL_REVISION", "backend returned unbound canonical revision"
        )
    if result.canonical_revision > request.current_canonical_revision:
        raise SemanticProjectionError(
            PROJECTION_STALE, "backend returned a future canonical revision"
        )
    _strict_hash(result.content_hash, "content_hash")
    _strict_hash(result.projection_text_hash, "projection_text_hash")
    if result.content_hash == result.projection_text_hash:
        raise SemanticProjectionError(
            "RESULT_HASH_BINDING_INVALID",
            "content hash and projection text hash are distinct bindings",
        )
    expected_provenance = {
        "canonical_ref": result.canonical_ref,
        "canonical_revision": result.canonical_revision,
        "value_hash": result.content_hash,
        "content_hash": result.content_hash,
        "source_kind": "canonical_sqlite",
    }
    if tuple(result.provenance) != (expected_provenance,):
        raise SemanticProjectionError(
            "INVALID_PROVENANCE", "backend returned unbound provenance"
        )
    return replace(result, metadata=metadata, provenance=(expected_provenance,))
