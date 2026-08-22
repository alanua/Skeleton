from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

from core.capability_model import PredictedImpact, classify_capability_impact


TASK_SPEC_SCHEMA = "skeleton.runner_task.v1"
PUBLIC_SAFE_PRIVACY_BOUNDARIES = frozenset(
    {
        "PUBLIC_SAFE_REPOSITORY_ONLY",
        "PUBLIC_SAFE_AGGREGATE_ONLY",
        "PUBLIC_SAFE_CONTROL_AND_EVIDENCE_METADATA_ONLY",
    }
)
PRIVATE_PRIVACY_BOUNDARIES = frozenset({"LOCAL_PRIVATE", "PRIVATE_LOCAL"})
ALLOWED_TASK_KINDS = frozenset(
    {
        "code_edit",
        "code_generation",
        "repository_maintenance",
        "private_memory",
        "diagnostic",
        "loop_control",
        "publish",
    }
)
ALLOWED_CAPABILITIES = frozenset(
    {
        "repository_read",
        "repository_write",
        "repository_write_allowlisted",
        "test_execution",
        "subprocess_isolated",
        "memory_gateway_read",
        "memory_gateway_write",
        "diagnostic_read",
        "repository_maintenance",
        "loop_control",
        "publish_pull_request",
    }
)


class TaskSpecError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class TaskSpec:
    schema: str
    repo: str
    branch: str
    task_kind: str
    payload: Mapping[str, Any]
    requested_capabilities: tuple[str, ...]
    allowed_files: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    validation_commands: tuple[tuple[str, ...], ...]
    expected_output: tuple[str, ...]
    privacy_boundary: str
    idempotency_key: str
    predicted_impact: PredictedImpact


def normalize_task_spec(value: str | Mapping[str, object]) -> TaskSpec:
    raw = _load_mapping(value)
    if raw.get("schema") != TASK_SPEC_SCHEMA:
        raise TaskSpecError("INVALID_TASKSPEC_SCHEMA")
    repo = _required_string(raw, "repo")
    if "/" not in repo:
        raise TaskSpecError("INVALID_TASKSPEC_REPO")
    branch = _required_string(raw, "branch")
    task_kind = _enum(raw, "task_kind", ALLOWED_TASK_KINDS)
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        raise TaskSpecError("INVALID_TASKSPEC_PAYLOAD")
    requested_capabilities = _string_tuple(raw.get("requested_capabilities"), "requested_capabilities")
    unknown_capabilities = set(requested_capabilities) - ALLOWED_CAPABILITIES
    if unknown_capabilities:
        raise TaskSpecError("UNKNOWN_TASKSPEC_CAPABILITY")
    allowed_files = _allowed_files(raw.get("allowed_files"))
    forbidden_actions = _string_tuple(raw.get("forbidden_actions", ()), "forbidden_actions")
    validation_commands = _validation_commands(raw.get("validation_commands", raw.get("validation", ())))
    expected_output = _string_tuple(raw.get("expected_output", ()), "expected_output")
    privacy_boundary = _privacy(raw.get("privacy_boundary"))
    idempotency_key = _required_string(raw, "idempotency_key")
    if len(idempotency_key) > 160 or "/" in idempotency_key or ".." in idempotency_key:
        raise TaskSpecError("INVALID_TASKSPEC_IDEMPOTENCY_KEY")
    predicted = classify_capability_impact(
        requested_capabilities=tuple(sorted(requested_capabilities)),
        allowed_files=tuple(sorted(allowed_files)),
        privacy_boundary=privacy_boundary,
    )
    return TaskSpec(
        schema=TASK_SPEC_SCHEMA,
        repo=repo,
        branch=branch,
        task_kind=task_kind,
        payload=_freeze_payload(payload),
        requested_capabilities=tuple(sorted(requested_capabilities)),
        allowed_files=tuple(sorted(allowed_files)),
        forbidden_actions=tuple(sorted(forbidden_actions)),
        validation_commands=validation_commands,
        expected_output=tuple(sorted(expected_output)),
        privacy_boundary=privacy_boundary,
        idempotency_key=f"{repo}:{idempotency_key}",
        predicted_impact=predicted,
    )


def _load_mapping(value: str | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    try:
        raw = yaml.safe_load(value)
    except yaml.YAMLError as exc:
        raise TaskSpecError("MALFORMED_TASKSPEC_YAML") from exc
    if not isinstance(raw, Mapping):
        raise TaskSpecError("INVALID_TASKSPEC_ENVELOPE")
    return raw


def _required_string(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TaskSpecError(f"INVALID_TASKSPEC_{field.upper()}")
    return value.strip()


def _enum(raw: Mapping[str, object], field: str, allowed: frozenset[str]) -> str:
    value = _required_string(raw, field)
    if value not in allowed:
        raise TaskSpecError(f"INVALID_TASKSPEC_{field.upper()}")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TaskSpecError(f"INVALID_TASKSPEC_{field.upper()}")
    items = tuple(str(item).strip() for item in value)
    if any(not item for item in items):
        raise TaskSpecError(f"INVALID_TASKSPEC_{field.upper()}")
    return items


def _allowed_files(value: object) -> tuple[str, ...]:
    files = _string_tuple(value, "allowed_files")
    if not files:
        raise TaskSpecError("TASKSPEC_ALLOWED_FILES_REQUIRED")
    for path in files:
        if path.startswith("/") or "\\" in path or "\x00" in path:
            raise TaskSpecError("UNBOUNDED_TASKSPEC_ALLOWED_FILE")
        if "**" in PurePosixPath(path).parts or ".." in PurePosixPath(path).parts:
            raise TaskSpecError("UNBOUNDED_TASKSPEC_ALLOWED_FILE")
    return files


def _validation_commands(value: object) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TaskSpecError("INVALID_TASKSPEC_VALIDATION")
    commands: list[tuple[str, ...]] = []
    for item in value:
        if isinstance(item, str):
            commands.append(tuple(part for part in item.split() if part))
        elif isinstance(item, Sequence):
            commands.append(tuple(str(part) for part in item))
        else:
            raise TaskSpecError("INVALID_TASKSPEC_VALIDATION")
    return tuple(command for command in commands if command)


def _privacy(value: object) -> str:
    if not isinstance(value, str):
        raise TaskSpecError("INVALID_TASKSPEC_PRIVACY_BOUNDARY")
    privacy = value.strip().upper()
    if privacy not in PUBLIC_SAFE_PRIVACY_BOUNDARIES | PRIVATE_PRIVACY_BOUNDARIES:
        raise TaskSpecError("INVALID_TASKSPEC_PRIVACY_BOUNDARY")
    return privacy


def _freeze_payload(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return dict(value)
