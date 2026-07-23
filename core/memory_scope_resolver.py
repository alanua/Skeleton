from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


class MemoryScopeResolutionError(ValueError):
    """Raised when a private memory request scope is malformed or foreign."""


_SAFE_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class ResolvedMemoryScope:
    project_id: str
    dataset_id: str
    exact_keys: tuple[str, ...]
    query: str

    @property
    def scope_hash_material(self) -> str:
        return f"{self.project_id}\n{self.dataset_id}"


def resolve_private_memory_scope(request: Mapping[str, object]) -> ResolvedMemoryScope:
    project_id = _scope_token(request.get("project_id"), "project_id")
    dataset_id = _scope_token(request.get("dataset_id"), "dataset_id")
    if project_id != "skeleton":
        raise MemoryScopeResolutionError("foreign private project scope")
    if dataset_id != "skeleton" and not dataset_id.startswith("skeleton."):
        raise MemoryScopeResolutionError("foreign private dataset scope")

    raw_keys = request.get("exact_keys", ())
    if raw_keys is None:
        exact_keys: tuple[str, ...] = ()
    elif isinstance(raw_keys, list | tuple):
        exact_keys = tuple(_exact_key(item, dataset_id=dataset_id) for item in raw_keys)
    else:
        raise MemoryScopeResolutionError("exact_keys must be an array")

    query = request.get("query", "")
    if not isinstance(query, str) or len(query) > 512:
        raise MemoryScopeResolutionError("query must be bounded text")
    if "*" in project_id or "*" in dataset_id or any("*" in key for key in exact_keys):
        raise MemoryScopeResolutionError("wildcard private memory scope is forbidden")
    return ResolvedMemoryScope(project_id=project_id, dataset_id=dataset_id, exact_keys=exact_keys, query=query)


def _scope_token(value: object, field: str) -> str:
    if isinstance(value, str) and "*" in value:
        raise MemoryScopeResolutionError("wildcard private memory scope is forbidden")
    if not isinstance(value, str) or not _SAFE_SCOPE_RE.fullmatch(value):
        raise MemoryScopeResolutionError(f"{field} is malformed")
    return value


def _exact_key(value: object, *, dataset_id: str) -> str:
    if not isinstance(value, str) or ":" not in value:
        raise MemoryScopeResolutionError("exact key must be namespace:fact_id")
    namespace, fact_id = value.split(":", 1)
    namespace = _scope_token(namespace, "exact namespace")
    fact_id = _scope_token(fact_id, "exact fact_id")
    if namespace != dataset_id:
        raise MemoryScopeResolutionError("exact key is outside dataset scope")
    return f"{namespace}:{fact_id}"
