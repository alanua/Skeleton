"""Fail-closed device capability contract for the existing Skeleton executor.

This module never executes commands or resolves secrets. Approved plans must be
handed to the canonical Home Edge executor and verified there.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeviceRisk(str, Enum):
    READ_ONLY = "read_only"
    ROUTINE_MUTATION = "routine_mutation"
    PRIVILEGED_MUTATION = "privileged_mutation"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class DeviceActionPlan:
    device_id: str
    capability: str
    node_id: str
    execution_lane: DeviceRisk
    run_as: str
    timeout_seconds: int
    request_id: str
    idempotency_key: str
    argv: tuple[str, ...]
    operator_approval_ref: str | None
    requires_secret: bool = False
    secret_ref: str | None = None
    verification: str = ""
    rollback: str = ""


@dataclass(frozen=True)
class DeviceActionDecision:
    permitted: bool
    reasons: tuple[str, ...]


def validate_device_action(plan: DeviceActionPlan, *, approved_scope: str | None) -> DeviceActionDecision:
    """Check the plan before handing it to Home Edge. No side effects.

    Approval scope format is '<device_id>:<capability>' and must correspond
    to an explicit operator confirmation resolved by the canonical gateway.
    The scope string alone is NOT an approval token.
    """
    failures: list[str] = []
    for field in ("device_id", "capability", "request_id", "idempotency_key", "verification"):
        value = getattr(plan, field)
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            failures.append(f"{field}: required nonempty bounded string")
    if plan.node_id != "home-edge-01":
        failures.append("node_id: must use canonical Home Edge executor")
    if plan.run_as not in ("desktop-user", "root"):
        failures.append("run_as: unsupported principal")
    if not isinstance(plan.execution_lane, DeviceRisk):
        failures.append("execution_lane: invalid risk lane")
    if type(plan.timeout_seconds) is not int or not 1 <= plan.timeout_seconds <= 900:
        failures.append("timeout_seconds: must be in 1..900")
    if not plan.argv or any(not isinstance(x, str) or not x for x in plan.argv):
        failures.append("argv: nonempty exact argv required")
    if any(any(ch in part for ch in ("\n", "\r", "\x00")) for part in plan.argv):
        failures.append("argv: control character forbidden")
    if plan.requires_secret:
        if not plan.secret_ref or not plan.secret_ref.startswith("bitwarden-sm:"):
            failures.append("secret_ref: use Bitwarden opaque reference")
        if any("password=" in part.lower() or "token=" in part.lower() for part in plan.argv):
            failures.append("argv: may not embed secrets")
    if plan.execution_lane != DeviceRisk.READ_ONLY:
        if not plan.operator_approval_ref or not approved_scope:
            failures.append("operator consent: required")
        elif approved_scope != f"{plan.device_id}:{plan.capability}":
            failures.append("operator consent: scope mismatch")
    if plan.execution_lane == DeviceRisk.DESTRUCTIVE and not plan.rollback:
        failures.append("rollback: destructive actions must define rollback or fail closed")
    return DeviceActionDecision(not failures, tuple(failures))
