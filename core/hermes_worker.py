from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SAFE_STATUSES = {
    "DRY_RUN_OK",
    "REVIEW_REQUIRED",
    "OPERATOR_APPROVAL_REQUIRED",
    "BLOCKED",
}

SAFE_WORKER_MODES = {"review_only", "dry_run", "contract_test"}

REQUIRED_TASK_FIELDS = (
    "schema",
    "task_id",
    "title",
    "goal",
    "worker_mode",
    "public_safe",
    "no_secrets",
    "no_runtime_mutation",
    "approval_required",
    "source_context",
    "scope",
    "allowed_files",
    "forbidden_actions",
    "validation",
    "expected_outputs",
    "authority_boundary",
)

REQUIRED_SKILL_FIELDS = (
    "schema",
    "skill_id",
    "version",
    "name",
    "summary",
    "activation_state",
    "public_safe",
    "approval_required",
    "runtime_install_allowed",
    "network_required",
    "inputs",
    "outputs",
    "allowed_operations",
    "forbidden_operations",
    "authority_boundary",
)

PRIVATE_FIELD_MARKERS = (
    "credential",
    "hidden",
    "password",
    "private",
    "secret",
    "token",
)

PUBLIC_SAFE_FIELD_NAMES = {
    "no_secrets",
    "public_safe",
}

OPERATOR_APPROVAL_TIERS = {
    "operator_approval_required",
    "operator",
    "privileged",
    "restricted",
}


def run_hermes_worker_dry_run(
    task_packet: object,
    skill_manifest: object | None = None,
) -> dict[str, object]:
    """Return a public-safe dry-run decision for a Hermes Worker v0 packet."""

    task = _PlainReader(task_packet)
    skill = _PlainReader(skill_manifest) if skill_manifest is not None else None

    missing_task_fields = _missing_fields(task, REQUIRED_TASK_FIELDS)
    missing_skill_fields = (
        _missing_fields(skill, REQUIRED_SKILL_FIELDS) if skill is not None else []
    )

    warnings: list[str] = []
    invalid_fields: list[str] = []

    schema = task.get("schema")
    mode = task.get("worker_mode")

    if schema is not None and schema != "hermes.task_packet.v0":
        invalid_fields.append("schema")
    if mode is not None and mode not in SAFE_WORKER_MODES:
        invalid_fields.append("worker_mode")

    _require_const_true(task, invalid_fields, "public_safe")
    _require_const_true(task, invalid_fields, "no_secrets")
    _require_const_true(task, invalid_fields, "no_runtime_mutation")
    _require_const_true(task, invalid_fields, "approval_required")
    _validate_task_authority_boundary(task.get("authority_boundary"), invalid_fields)
    _validate_validation_commands(task.get("validation"), invalid_fields)

    if skill is not None:
        _validate_skill_manifest(skill, invalid_fields)

    private_fields = sorted(
        set(_private_field_names(task_packet))
        | (set(_private_field_names(skill_manifest)) if skill_manifest is not None else set())
    )
    if private_fields:
        warnings.append("private_or_sensitive_fields_redacted")

    status = _status_for(
        mode=mode,
        missing_fields=missing_task_fields + missing_skill_fields,
        invalid_fields=invalid_fields,
        skill=skill,
    )
    decision = _decision_for(status)

    return {
        "status": status,
        "task_id": _public_identifier(task.get("task_id")),
        "skill_id": _public_identifier(skill.get("skill_id") if skill else None),
        "mode": _public_identifier(mode),
        "decision": decision,
        "warnings": warnings,
        "diagnostics": {
            "schema": "hermes.worker_dry_run_result.v0",
            "safe_statuses": sorted(SAFE_STATUSES),
            "missing_fields": sorted(missing_task_fields + missing_skill_fields),
            "invalid_fields": sorted(set(invalid_fields)),
            "redacted_fields": private_fields,
        },
    }


def _missing_fields(reader: "_PlainReader | None", required: tuple[str, ...]) -> list[str]:
    if reader is None:
        return list(required)
    return [name for name in required if reader.get(name) is None]


def _require_const_true(
    reader: "_PlainReader", invalid_fields: list[str], field_name: str
) -> None:
    value = reader.get(field_name)
    if value is not None and value is not True:
        invalid_fields.append(field_name)


def _validate_task_authority_boundary(value: object, invalid_fields: list[str]) -> None:
    boundary = _PlainReader(value)
    expected = {
        "review_only": True,
        "mutation_allowed": False,
        "runtime_install_allowed": False,
    }
    for key, expected_value in expected.items():
        actual = boundary.get(key)
        if actual is not None and actual is not expected_value:
            invalid_fields.append(f"authority_boundary.{key}")


def _validate_validation_commands(value: object, invalid_fields: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not value:
        invalid_fields.append("validation")
        return
    for index, command in enumerate(value):
        reader = _PlainReader(command)
        if reader.get("mutating") is not False:
            invalid_fields.append(f"validation[{index}].mutating")


def _validate_skill_manifest(reader: "_PlainReader", invalid_fields: list[str]) -> None:
    if reader.get("schema") is not None and reader.get("schema") != "hermes.skill_manifest.v0":
        invalid_fields.append("skill.schema")

    activation_state = reader.get("activation_state")
    if activation_state is not None and activation_state not in {
        "proposed",
        "review_only",
        "disabled",
    }:
        invalid_fields.append("skill.activation_state")

    for field_name in ("public_safe", "approval_required"):
        value = reader.get(field_name)
        if value is not None and value is not True:
            invalid_fields.append(f"skill.{field_name}")

    for field_name in ("runtime_install_allowed", "network_required"):
        value = reader.get(field_name)
        if value is not None and value is not False:
            invalid_fields.append(f"skill.{field_name}")

    boundary = _PlainReader(reader.get("authority_boundary"))
    expected = {
        "review_only": True,
        "mutation_allowed": False,
        "activation_allowed": False,
    }
    for key, expected_value in expected.items():
        actual = boundary.get(key)
        if actual is not None and actual is not expected_value:
            invalid_fields.append(f"skill.authority_boundary.{key}")


def _status_for(
    *,
    mode: object,
    missing_fields: list[str],
    invalid_fields: list[str],
    skill: "_PlainReader | None",
) -> str:
    if mode is not None and mode not in SAFE_WORKER_MODES:
        return "BLOCKED"
    if any(field.endswith("no_runtime_mutation") for field in invalid_fields):
        return "BLOCKED"
    if _skill_requires_operator_approval(skill):
        return "OPERATOR_APPROVAL_REQUIRED"
    if missing_fields or invalid_fields:
        return "REVIEW_REQUIRED"
    return "DRY_RUN_OK"


def _skill_requires_operator_approval(skill: "_PlainReader | None") -> bool:
    if skill is None:
        return False
    for field_name in ("skill_tier", "tier", "approval_tier"):
        value = skill.get(field_name)
        if isinstance(value, str) and value in OPERATOR_APPROVAL_TIERS:
            return True
    return False


def _decision_for(status: str) -> dict[str, object]:
    if status == "DRY_RUN_OK":
        return {
            "allowed": True,
            "reason": "packet_satisfies_public_safe_dry_run_contract",
        }
    if status == "OPERATOR_APPROVAL_REQUIRED":
        return {
            "allowed": False,
            "reason": "skill_tier_requires_operator_approval",
        }
    if status == "BLOCKED":
        return {
            "allowed": False,
            "reason": "packet_requests_unsafe_or_live_execution",
        }
    return {
        "allowed": False,
        "reason": "packet_requires_review_before_dry_run",
    }


def _public_identifier(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _private_field_names(payload: object) -> list[str]:
    if payload is None:
        return []

    names: list[str] = []
    if isinstance(payload, Mapping):
        items = payload.items()
    else:
        try:
            items = vars(payload).items()
        except TypeError:
            return []

    for key, value in items:
        key_string = str(key)
        lowered = key_string.lower()
        if lowered in PUBLIC_SAFE_FIELD_NAMES:
            continue
        if any(marker in lowered for marker in PRIVATE_FIELD_MARKERS):
            names.append(key_string)
            continue
        if isinstance(value, Mapping) or hasattr(value, "__dict__"):
            names.extend(f"{key_string}.{name}" for name in _private_field_names(value))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                names.extend(
                    f"{key_string}[{index}].{name}" for name in _private_field_names(item)
                )
    return names


class _PlainReader:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def get(self, name: str) -> object:
        if isinstance(self._payload, Mapping):
            return self._payload.get(name)
        return getattr(self._payload, name, None)
