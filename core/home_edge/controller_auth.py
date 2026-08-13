from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


AUTHORITY_MODE = "RUNTIME_MAINTENANCE_TASK"
DISPLAY_POWER_OFF_TASK_ID = "home_edge_01_display_power_off_v1"
AUTHORITY_RISK = "yellow"
AUTHORITY_TARGET_NODE = "home-edge-01"
AUTHORITY_OPERATOR_APPROVAL = "EXPLICIT_2026_08_09_TURN_OFF_HOME_EDGE_MONITOR"
AUTHORITY_PRIVACY_BOUNDARY = "PRIVATE_RUNTIME_STATE_PUBLIC_SAFE_STATUS"

AUTHORITY_FIELDS: Mapping[str, str] = {
    "Mode": AUTHORITY_MODE,
    "Maintenance Task ID": DISPLAY_POWER_OFF_TASK_ID,
    "Risk": AUTHORITY_RISK,
    "Target Node": AUTHORITY_TARGET_NODE,
    "Operator Approval": AUTHORITY_OPERATOR_APPROVAL,
    "Privacy Boundary": AUTHORITY_PRIVACY_BOUNDARY,
}

AUTHORITY_FIELD_NAMES = frozenset(AUTHORITY_FIELDS)
MAX_AUTHORITY_BODY_BYTES = 16_384


class HomeEdgeAuthorityError(ValueError):
    """Raised when a runtime maintenance issue body does not match authority."""


@dataclass(frozen=True)
class DisplayOffAuthority:
    mode: str
    maintenance_task_id: str
    risk: str
    target_node: str
    operator_approval: str
    privacy_boundary: str

    def to_mapping(self) -> dict[str, str]:
        return {
            "Mode": self.mode,
            "Maintenance Task ID": self.maintenance_task_id,
            "Risk": self.risk,
            "Target Node": self.target_node,
            "Operator Approval": self.operator_approval,
            "Privacy Boundary": self.privacy_boundary,
        }


def literal_display_off_authority() -> DisplayOffAuthority:
    return DisplayOffAuthority(
        mode=AUTHORITY_MODE,
        maintenance_task_id=DISPLAY_POWER_OFF_TASK_ID,
        risk=AUTHORITY_RISK,
        target_node=AUTHORITY_TARGET_NODE,
        operator_approval=AUTHORITY_OPERATOR_APPROVAL,
        privacy_boundary=AUTHORITY_PRIVACY_BOUNDARY,
    )


def validate_display_off_authority(body: str) -> DisplayOffAuthority:
    if not isinstance(body, str):
        raise HomeEdgeAuthorityError("authority body must be text")
    if len(body.encode("utf-8")) > MAX_AUTHORITY_BODY_BYTES:
        raise HomeEdgeAuthorityError("authority body exceeds bounded input limit")

    observed: dict[str, str] = {}
    for line in body.splitlines():
        match = re.fullmatch(r"([A-Za-z ]+):[ \t]*(.*)", line.strip())
        if match is None:
            continue
        name, value = match.groups()
        if name in AUTHORITY_FIELD_NAMES:
            if name in observed:
                raise HomeEdgeAuthorityError(f"duplicate authority field: {name}")
            observed[name] = value

    for name, expected in AUTHORITY_FIELDS.items():
        actual = observed.get(name)
        if actual != expected:
            raise HomeEdgeAuthorityError(f"authority {name} mismatch")

    return literal_display_off_authority()
