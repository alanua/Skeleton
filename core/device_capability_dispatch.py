"""Universal device operation planner using the canonical Home Edge gateway.

The resolver and approval verifier are supplied by Skeleton runtime. This module
never retrieves secrets, creates alternative execution channels or treats text
from a chat as a gateway approval receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from core.device_capability_contract import (
    DeviceActionPlan, DeviceActionDecision, DeviceRisk, validate_device_action,
)


class DeviceCapabilityError(RuntimeError):
    pass


class ApprovalVerifier(Protocol):
    def __call__(self, approval_ref: str, device_id: str, capability: str, lane: str) -> bool: ...


class CapabilityResolver(Protocol):
    def __call__(self, device_id: str, capability: str) -> DeviceActionPlan: ...


class CanonicalExecutor(Protocol):
    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class DeviceOperationResult:
    device_id: str
    capability: str
    status: str
    receipt: Mapping[str, Any]
    verified: bool


def prepare_home_edge_request(
    plan: DeviceActionPlan,
    *,
    approval_verifier: ApprovalVerifier,
) -> dict[str, Any]:
    """Bind a resolved capability to existing gateway request semantics.

    Crucially approval_verifier must check an authentic, current, scoped
    approval receipt against Skeleton's policy store; no caller-supplied
    scope string is trusted as approval.
    """
    if not isinstance(plan, DeviceActionPlan):
        raise DeviceCapabilityError("invalid device action plan")
    approved_scope = None
    if plan.execution_lane != DeviceRisk.READ_ONLY:
        if not plan.operator_approval_ref:
            raise DeviceCapabilityError("missing operator approval")
        try:
            authentic = approval_verifier(
                plan.operator_approval_ref, plan.device_id,
                plan.capability, plan.execution_lane.value,
            )
        except Exception as exc:
            raise DeviceCapabilityError("approval verification unavailable") from exc
        if authentic is not True:
            raise DeviceCapabilityError("approval receipt invalid or out of scope")
        approved_scope = f"{plan.device_id}:{plan.capability}"
    decision: DeviceActionDecision = validate_device_action(
        plan, approved_scope=approved_scope
    )
    if not decision.permitted:
        raise DeviceCapabilityError("; ".join(decision.reasons))
    # The transport receives only opaque secret refs; the executable specified
    # in argv must be separately reviewed to fetch via a trusted secret broker.
    request = {
        "node_id": plan.node_id,
        "mode": "argv",
        "argv": list(plan.argv),
        "run_as": plan.run_as,
        "execution_lane": plan.execution_lane.value,
        "timeout_seconds": plan.timeout_seconds,
        "request_id": plan.request_id,
        "idempotency_key": plan.idempotency_key,
    }
    if plan.operator_approval_ref:
        request["operator_approval_ref"] = plan.operator_approval_ref
    return request


def execute_device_capability(
    device_id: str,
    capability: str,
    *,
    resolve: CapabilityResolver,
    verify_approval: ApprovalVerifier,
    execute_home_edge: CanonicalExecutor,
    verify_postcondition: Callable[[DeviceActionPlan, Mapping[str, Any]], bool],
) -> DeviceOperationResult:
    """Execute via canonical Home Edge; fail closed on missing verification.

    A successful command is merely accepted/applied evidence, not proof that
    a motor moved or a printhead was cleaned.
    """
    plan = resolve(device_id, capability)
    if plan.device_id != device_id or plan.capability != capability:
        raise DeviceCapabilityError("resolved operation identity mismatch")
    request = prepare_home_edge_request(plan, approval_verifier=verify_approval)
    receipt = execute_home_edge(request)
    if receipt.get("status") != "ok" or receipt.get("idempotency") == "conflict":
        return DeviceOperationResult(device_id, capability, "execution_failed", receipt, False)
    verified = verify_postcondition(plan, receipt) is True
    return DeviceOperationResult(
        device_id, capability,
        "physically_verified" if verified else "accepted_unverified",
        receipt, verified,
    )
