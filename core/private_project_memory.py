from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


PROJECT_MEMORY_STATUS_SCHEMA = "skeleton.private_project_memory.project_status.v0"
PROJECT_MEMORY_REGISTRY_SUMMARY_SCHEMA = "skeleton.private_project_memory.registry_summary.v0"

PROJECT_MEMORY_STATES = ("active", "paused", "blocked", "archived", "unknown")
PROJECT_MEMORY_ATTENTION_STATES = ("none", "review", "operator", "blocked", "unknown")
PROJECT_MEMORY_NEXT_ACTIONS = (
    "none",
    "configure_project_memory_registry",
    "review_blocked_project_memory",
    "refresh_stale_project_memory",
    "review_project_memory_attention",
    "initialize_project_memory_schema",
)

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_UNSAFE_KEY_PARTS = (
    "branch",
    "content",
    "credential",
    "db",
    "drive",
    "env",
    "excerpt",
    "file",
    "name",
    "path",
    "payload",
    "provider",
    "repo",
    "secret",
    "sql",
    "task_title",
    "token",
    "url",
)
_UNSAFE_VALUE_MARKERS = (
    "/",
    "\\",
    "file:",
    ".sqlite",
    ".db",
    "github.com",
    "drive.google.com",
    "secret",
    "token",
    "password",
    "credential",
)


@dataclass(frozen=True)
class ProjectMemoryRegistrySummary:
    schema: str
    status: str
    project_count: int
    state_counts: dict[str, int]
    attention_counts: dict[str, int]
    schema_ready_count: int
    stale_project_count: int
    blocked_project_count: int
    total_task_backlog_count: int
    total_open_decision_count: int
    error_class: str | None
    next_operator_action: str


def summarize_project_memory_registry(records: Iterable[Mapping[str, Any]] | None) -> dict[str, object]:
    """Return a public-safe aggregate project memory summary.

    Records must already be local aggregate project status records. This function
    does not load files or expose per-project records.
    """
    try:
        if records is None:
            raise PrivateProjectMemoryConfigError("missing registry records")
        summary = _summarize_records(records)
        return _sanitize_summary(summary)
    except Exception as exc:  # noqa: BLE001 - reports must fail closed.
        return _blocked_summary(type(exc).__name__)


def _summarize_records(records: Iterable[Mapping[str, Any]]) -> ProjectMemoryRegistrySummary:
    state_counts = _zero_counts(PROJECT_MEMORY_STATES)
    attention_counts = _zero_counts(PROJECT_MEMORY_ATTENTION_STATES)
    project_count = 0
    schema_ready_count = 0
    stale_project_count = 0
    blocked_project_count = 0
    total_task_backlog_count = 0
    total_open_decision_count = 0

    for record in records:
        normalized = _validate_project_status(record)
        project_count += 1
        state_counts[normalized["state"]] += 1
        attention_counts[normalized["attention"]] += 1
        if normalized["schema_ready"]:
            schema_ready_count += 1
        if normalized["stale"]:
            stale_project_count += 1
        if normalized["state"] == "blocked" or normalized["attention"] == "blocked":
            blocked_project_count += 1
        total_task_backlog_count += normalized["task_backlog_count"]
        total_open_decision_count += normalized["open_decision_count"]

    next_action = _next_action(
        project_count=project_count,
        schema_ready_count=schema_ready_count,
        blocked_project_count=blocked_project_count,
        stale_project_count=stale_project_count,
        attention_counts=attention_counts,
    )
    return ProjectMemoryRegistrySummary(
        schema=PROJECT_MEMORY_REGISTRY_SUMMARY_SCHEMA,
        status="DONE",
        project_count=project_count,
        state_counts=state_counts,
        attention_counts=attention_counts,
        schema_ready_count=schema_ready_count,
        stale_project_count=stale_project_count,
        blocked_project_count=blocked_project_count,
        total_task_backlog_count=total_task_backlog_count,
        total_open_decision_count=total_open_decision_count,
        error_class=None,
        next_operator_action=next_action,
    )


def _validate_project_status(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise PrivateProjectMemoryConfigError("project status must be an object")
    _reject_unsafe_keys(record)

    if record.get("schema") != PROJECT_MEMORY_STATUS_SCHEMA:
        raise PrivateProjectMemoryConfigError("invalid project status schema")

    project_ref = record.get("project_ref")
    if not isinstance(project_ref, str) or not _SAFE_TOKEN_RE.fullmatch(project_ref):
        raise PrivateProjectMemoryPrivacyError("unsafe project reference")
    if _looks_private(project_ref):
        raise PrivateProjectMemoryPrivacyError("unsafe project reference")

    state = record.get("state")
    if state not in PROJECT_MEMORY_STATES:
        raise PrivateProjectMemoryConfigError("invalid project state")
    attention = record.get("attention")
    if attention not in PROJECT_MEMORY_ATTENTION_STATES:
        raise PrivateProjectMemoryConfigError("invalid attention state")

    schema_ready = _bool_value(record.get("schema_ready"), "schema_ready")
    stale = _bool_value(record.get("stale"), "stale")
    task_backlog_count = _count_value(record.get("task_backlog_count"), "task_backlog_count")
    open_decision_count = _count_value(record.get("open_decision_count"), "open_decision_count")

    return {
        "project_ref": project_ref,
        "state": state,
        "attention": attention,
        "schema_ready": schema_ready,
        "stale": stale,
        "task_backlog_count": task_backlog_count,
        "open_decision_count": open_decision_count,
    }


def _next_action(
    *,
    project_count: int,
    schema_ready_count: int,
    blocked_project_count: int,
    stale_project_count: int,
    attention_counts: Mapping[str, int],
) -> str:
    if project_count == 0:
        return "configure_project_memory_registry"
    if schema_ready_count < project_count:
        return "initialize_project_memory_schema"
    if blocked_project_count:
        return "review_blocked_project_memory"
    if stale_project_count:
        return "refresh_stale_project_memory"
    if attention_counts["operator"] or attention_counts["review"]:
        return "review_project_memory_attention"
    return "none"


def _blocked_summary(error_class: str) -> dict[str, object]:
    summary = ProjectMemoryRegistrySummary(
        schema=PROJECT_MEMORY_REGISTRY_SUMMARY_SCHEMA,
        status="BLOCKED",
        project_count=0,
        state_counts=_zero_counts(PROJECT_MEMORY_STATES),
        attention_counts=_zero_counts(PROJECT_MEMORY_ATTENTION_STATES),
        schema_ready_count=0,
        stale_project_count=0,
        blocked_project_count=0,
        total_task_backlog_count=0,
        total_open_decision_count=0,
        error_class=error_class,
        next_operator_action="configure_project_memory_registry",
    )
    return _sanitize_summary(summary)


def _sanitize_summary(summary: ProjectMemoryRegistrySummary) -> dict[str, object]:
    data = asdict(summary)
    _reject_unsafe_keys(data)
    _reject_unsafe_values(data)
    if data["next_operator_action"] not in PROJECT_MEMORY_NEXT_ACTIONS:
        raise PrivateProjectMemoryPrivacyError("unsafe next action")
    return data


def _zero_counts(keys: Iterable[str]) -> dict[str, int]:
    return {key: 0 for key in keys}


def _bool_value(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise PrivateProjectMemoryConfigError(f"{name} must be boolean")
    return value


def _count_value(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PrivateProjectMemoryConfigError(f"{name} must be a non-negative integer")
    return value


def _reject_unsafe_keys(mapping: Mapping[str, Any]) -> None:
    for key, value in mapping.items():
        lowered = str(key).lower()
        if any(part in lowered for part in _UNSAFE_KEY_PARTS):
            raise PrivateProjectMemoryPrivacyError("unsafe registry key")
        if isinstance(value, Mapping):
            _reject_unsafe_keys(value)


def _reject_unsafe_values(value: object) -> None:
    if isinstance(value, str) and _looks_private(value):
        raise PrivateProjectMemoryPrivacyError("unsafe registry value")
    if isinstance(value, Mapping):
        for nested in value.values():
            _reject_unsafe_values(nested)
    if isinstance(value, list):
        for nested in value:
            _reject_unsafe_values(nested)


def _looks_private(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _UNSAFE_VALUE_MARKERS)


class PrivateProjectMemoryError(Exception):
    """Base exception for project memory registry failures."""


class PrivateProjectMemoryConfigError(PrivateProjectMemoryError):
    """Raised when synthetic registry input is missing or invalid."""


class PrivateProjectMemoryPrivacyError(PrivateProjectMemoryError):
    """Raised when registry input or output would expose private details."""
