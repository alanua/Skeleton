from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.cognee_projection_adapter import CogneeProjectionAdapter
from core.graphify_adapter import LocalGraphifyIndex
from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_policy import MemoryGatewayPolicyError
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.memory_scope_resolver import MemoryScopeResolutionError, ResolvedMemoryScope, resolve_private_memory_scope
from core.mempalace_adapter import LocalMemPalaceIndex
from core.private_memory_stack import PRIVATE_MEMORY_STACK_ROOT_ENV, PrivateMemoryStack
from core.private_memory_history import bytes_hash, canonical_json
from core.semantic_memory_projection import SEMANTIC_RECALL_REQUEST_SCHEMA, SemanticProjectionError


MEMORY_BOOTSTRAP_REQUEST_SCHEMA = "skeleton.memory_bootstrap.request.v1"
MEMORY_BOOTSTRAP_RESPONSE_SCHEMA = "skeleton.memory_bootstrap.response.v1"
PRIVATE_MEMORY_CONTEXT_MARKER = "SKELETON_PRIVATE_MEMORY_CONTEXT_V1"


class MemoryBootstrapError(RuntimeError):
    """Raised when local-private memory bootstrap must fail closed."""


@dataclass(frozen=True)
class MemoryBootstrapResult:
    schema: str
    status: str
    project_id: str
    dataset_id: str
    canonical_revision: int
    context: dict[str, object]
    public_receipt: dict[str, object]


_BOOTSTRAP_CACHE: dict[str, MemoryBootstrapResult] = {}
_STACK_CACHE: dict[str, tuple[PrivateMemoryStack, PrivateMemoryGatewayStorage, MemoryGateway]] = {}


def clear_memory_bootstrap_cache() -> None:
    _BOOTSTRAP_CACHE.clear()
    _STACK_CACHE.clear()


def private_memory_bootstrap(
    request: Mapping[str, object],
    *,
    private_root: str | Path | None = None,
    cognee_adapter: CogneeProjectionAdapter | None = None,
) -> MemoryBootstrapResult:
    if request.get("schema") != MEMORY_BOOTSTRAP_REQUEST_SCHEMA:
        raise MemoryBootstrapError("invalid memory bootstrap request schema")
    try:
        scope = resolve_private_memory_scope(request)
    except MemoryScopeResolutionError as exc:
        raise MemoryBootstrapError(str(exc)) from exc
    root = _private_root(private_root)
    stack, storage, gateway = _stack_for_root(root)
    status = _gateway_status(gateway, scope)
    canonical_revision = int(status["canonical_revision"])
    cache_key = _cache_key(scope, canonical_revision)
    cached = _BOOTSTRAP_CACHE.get(cache_key)
    if cached is not None:
        return cached

    exact_items = _exact_items(gateway, scope)
    cognee = _cognee_context(
        cognee_adapter,
        scope=scope,
        query=scope.query,
        canonical_revision=canonical_revision,
    )
    mempalace = _mempalace_context(stack, scope=scope, query=scope.query, canonical_revision=canonical_revision)
    graphify = _graphify_context(stack, scope=scope, query=scope.query, canonical_revision=canonical_revision)
    selected = "cognee" if cognee is not None else ("mempalace" if mempalace is not None else "none")
    context = {
        "schema": "skeleton.private_memory_context.v1",
        "marker": PRIVATE_MEMORY_CONTEXT_MARKER,
        "project_id": scope.project_id,
        "dataset_id": scope.dataset_id,
        "canonical_revision": canonical_revision,
        "exact": exact_items,
        "cognee": cognee,
        "mempalace": mempalace,
        "graphify": graphify,
        "selected_projection": selected,
    }
    receipt = {
        "schema": MEMORY_BOOTSTRAP_RESPONSE_SCHEMA,
        "status": "READY",
        "project_id": scope.project_id,
        "dataset_id": scope.dataset_id,
        "canonical_revision": canonical_revision,
        "aggregate_counts": {
            "record_count": len(exact_items),
            "stale_count": int(mempalace is None) + int(graphify is None),
        },
        "selected_projection": selected,
        "public_safe": True,
    }
    result = MemoryBootstrapResult(
        schema=MEMORY_BOOTSTRAP_RESPONSE_SCHEMA,
        status="READY",
        project_id=scope.project_id,
        dataset_id=scope.dataset_id,
        canonical_revision=canonical_revision,
        context=context,
        public_receipt=receipt,
    )
    _BOOTSTRAP_CACHE[cache_key] = result
    return result


def _private_root(private_root: str | Path | None) -> Path:
    raw = private_root or os.environ.get(PRIVATE_MEMORY_STACK_ROOT_ENV)
    if raw is None:
        raise MemoryBootstrapError("MEMORY_UNAVAILABLE: private root is not configured")
    root = Path(raw)
    if not root.is_absolute():
        raise MemoryBootstrapError("MEMORY_UNAVAILABLE: private root must be absolute")
    return root


def _stack_for_root(root: Path) -> tuple[PrivateMemoryStack, PrivateMemoryGatewayStorage, MemoryGateway]:
    cached = _STACK_CACHE.get(str(root))
    if cached is not None:
        return cached
    stack = PrivateMemoryStack(root)
    if not stack.paths.db.is_file():
        raise MemoryBootstrapError("MEMORY_UNAVAILABLE: private storage is not initialized")
    storage = PrivateMemoryGatewayStorage(stack)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=storage,
    )
    composed = (stack, storage, gateway)
    _STACK_CACHE[str(root)] = composed
    return composed


def _gateway_status(gateway: MemoryGateway, scope: ResolvedMemoryScope) -> dict[str, object]:
    try:
        response = gateway.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": "skeleton",
                "command": "skeleton.memory.private_status",
                "payload": {"project_id": scope.project_id, "dataset_id": scope.dataset_id},
            }
        )
    except MemoryGatewayPolicyError as exc:
        raise MemoryBootstrapError(f"MEMORY_UNAVAILABLE: {exc.reason_code}") from exc
    payload = response["payload"]
    if not isinstance(payload, Mapping) or payload.get("status") != "READY":
        raise MemoryBootstrapError("MEMORY_UNAVAILABLE: private storage is not ready")
    return dict(payload)


def _exact_items(gateway: MemoryGateway, scope: ResolvedMemoryScope) -> list[dict[str, object]]:
    if scope.exact_keys:
        items = []
        for key in scope.exact_keys:
            response = gateway.execute(
                {
                    "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                    "namespace": "skeleton",
                    "command": "skeleton.memory.private_lookup_exact",
                    "payload": {"project_id": scope.project_id, "dataset_id": scope.dataset_id, "key": key},
                }
            )
            items.append(dict(response["payload"]))
        return items
    response = gateway.execute(
        {
            "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
            "namespace": "skeleton",
            "command": "skeleton.memory.private_list_exact",
            "payload": {"project_id": scope.project_id, "dataset_id": scope.dataset_id, "limit": 8},
        }
    )
    payload = response["payload"]
    if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
        raise MemoryBootstrapError("MEMORY_UNAVAILABLE: private exact list is unavailable")
    return [dict(item) for item in payload["results"] if isinstance(item, Mapping)]


def _cognee_context(
    adapter: CogneeProjectionAdapter | None,
    *,
    scope: ResolvedMemoryScope,
    query: str,
    canonical_revision: int,
) -> dict[str, object] | None:
    if adapter is None or not query.strip():
        return None
    try:
        health = adapter.health(
            project_id=scope.project_id,
            dataset_id=scope.dataset_id,
            current_canonical_revision=canonical_revision,
        )
        if health.get("status") != "READY":
            return None
        request = {
            "schema": SEMANTIC_RECALL_REQUEST_SCHEMA,
            "project_id": scope.project_id,
            "dataset_id": scope.dataset_id,
            "query": query,
            "current_canonical_revision": canonical_revision,
            "limit": 5,
        }
        return {"request": request, "response": adapter.recall(request), "fresh": True}
    except SemanticProjectionError:
        return None


def _mempalace_context(
    stack: PrivateMemoryStack,
    *,
    scope: ResolvedMemoryScope,
    query: str,
    canonical_revision: int,
) -> dict[str, object] | None:
    status = LocalMemPalaceIndex.status(stack.paths.mempalace, current_canonical_revision=canonical_revision)
    if status.get("state") != "READY" or not query.strip():
        return None
    return {"status": status, "response": stack.search(query=query, limit=5), "fresh": True}


def _graphify_context(
    stack: PrivateMemoryStack,
    *,
    scope: ResolvedMemoryScope,
    query: str,
    canonical_revision: int,
) -> dict[str, object] | None:
    status = LocalGraphifyIndex.status(stack.paths.graphify, current_canonical_revision=canonical_revision)
    if status.get("state") != "READY" or status.get("indexed_canonical_revision") != canonical_revision or not query.strip():
        return None
    return {"status": status, "response": stack.relations(query=query, limit=5), "fresh": True}


def _cache_key(scope: ResolvedMemoryScope, canonical_revision: int) -> str:
    payload = {
        "scope": bytes_hash(scope.scope_hash_material.encode("utf-8")),
        "canonical_revision": canonical_revision,
        "query": hashlib.sha256(scope.query.encode("utf-8")).hexdigest(),
        "exact_keys": hashlib.sha256(canonical_json(scope.exact_keys).encode("utf-8")).hexdigest(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
