from __future__ import annotations

import re


RUNTIME_MAINTENANCE_MODE = "RUNTIME_MAINTENANCE_TASK"
DISPLAY_POWER_OFF_TASK_ID = "home_edge_01_display_power_off_controller_v1"
DISPLAY_POWER_OFF_OPERATOR_APPROVAL = (
    "EXPLICIT_HOME_EDGE_01_DISPLAY_POWER_OFF_CONTROLLER_20260809"
)
DISPLAY_POWER_OFF_TARGET_NODE = "home-edge-01"
DISPLAY_POWER_OFF_PRIVACY_BOUNDARY = (
    "PRIVATE_CONTROLLER_CREDENTIAL / PUBLIC_SAFE_STATUS_ONLY"
)
DISPLAY_POWER_OFF_RISK = "yellow"


def body_field(body: str, field: str) -> str | None:
    match = re.search(
        rf"^\s*{re.escape(field)}:\s*(?P<value>\S(?:.*\S)?)\s*$",
        body or "",
        re.MULTILINE,
    )
    return match.group("value") if match else None


def require_exact_field(body: str, field: str, expected: str, reason: str) -> None:
    if body_field(body, field) != expected:
        raise ValueError(reason)


def validate_display_power_off_authority_header(body: str) -> None:
    require_exact_field(
        body,
        "Mode",
        RUNTIME_MAINTENANCE_MODE,
        "invalid_mode",
    )
    require_exact_field(
        body,
        "Maintenance Task ID",
        DISPLAY_POWER_OFF_TASK_ID,
        "unsupported_maintenance_task_id",
    )
    require_exact_field(
        body,
        "Operator Approval",
        DISPLAY_POWER_OFF_OPERATOR_APPROVAL,
        "missing_operator_approval",
    )
    require_exact_field(
        body,
        "Target Node",
        DISPLAY_POWER_OFF_TARGET_NODE,
        "unsupported_target_node",
    )
    require_exact_field(
        body,
        "Privacy Boundary",
        DISPLAY_POWER_OFF_PRIVACY_BOUNDARY,
        "unsupported_privacy_boundary",
    )
    risk = body_field(body, "Risk")
    if risk != DISPLAY_POWER_OFF_RISK:
        raise ValueError("unsupported_risk")
