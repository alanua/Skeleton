from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


_SAFE_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class MemoryScope:
    namespace: str
    project_id: str
    dataset_id: str


def resolve_memory_scope(
    values: Mapping[str, object] | None = None,
    *,
    namespace: object = None,
    project_id: object = None,
    dataset_id: object = None,
) -> MemoryScope:
    source = values or {}
    resolved_namespace = _safe_scope(namespace if namespace is not None else source.get("namespace"), "namespace")
    resolved_project_id = _safe_scope(project_id if project_id is not None else source.get("project_id"), "project_id")
    resolved_dataset_id = _safe_scope(
        dataset_id if dataset_id is not None else source.get("dataset_id", resolved_project_id),
        "dataset_id",
    )
    return MemoryScope(
        namespace=resolved_namespace,
        project_id=resolved_project_id,
        dataset_id=resolved_dataset_id,
    )


def _safe_scope(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_SCOPE_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be an exact bounded scope id")
    if value in {"*", "all", "any"}:
        raise ValueError(f"{field} must not be a wildcard")
    return value
