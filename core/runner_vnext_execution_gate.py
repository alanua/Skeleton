from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Mapping, Protocol

from core.runner_vnext_adapters import ExecutionEnvelope
from core.runner_vnext_control_plane import GreenExecutionHandoff
from core.runner_vnext_environment import EnvironmentContractError, LaneEnvironmentPolicyRegistry
from core.runner_vnext_ledger import LedgerError, OperationLedger
from core.runner_vnext_pep import PEPAuthorizationReceipt, PEPError, PolicyEnforcementPoint, reservation_scope_fingerprint
from core.runner_vnext_routing import NodeCapabilityRegistry, RoutingError
from core.runner_vnext_scheduler import VNextScheduler


class ExecutionGateError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class TargetStateVerifier(Protocol):
    def current_ref(self, handoff: GreenExecutionHandoff) -> str: ...


@dataclass(frozen=True)
class GreenExecutionGrant:
    task_id: str
    node_id: str
    node_generation: int
    node_snapshot_hash: str
    node_attestation_ref: str
    lane: str
    lease_scope_key: str = field(repr=False)
    fence_token: int
    environment_policy_hash: str
    operation_id: str
    idempotency_key: str
    adapter_id: str
    target_state_ref: str = field(repr=False)
    reservation_scope_hash: str
    planner_envelope_hash: str
    policy_reason_code: str
    authorization_status: str
    environment: Mapping[str, str] = field(repr=False)
    planner_envelope: ExecutionEnvelope = field(repr=False)
    execution_authorized: bool = True
    effect_execution_started: bool = False


class GreenExecutionGate:
    """Just-in-time exact-state gate. It grants execution but performs no effect."""

    def __init__(
        self,
        *,
        nodes: NodeCapabilityRegistry,
        environment_registry: LaneEnvironmentPolicyRegistry,
        scheduler: VNextScheduler,
        ledger: OperationLedger,
        target_state_verifier: TargetStateVerifier,
        pep: PolicyEnforcementPoint | None = None,
    ) -> None:
        self._nodes = nodes
        self._environment_registry = environment_registry
        self._scheduler = scheduler
        self._ledger = ledger
        self._target_state_verifier = target_state_verifier
        self._pep = pep or PolicyEnforcementPoint()

    def grant(self, handoff: GreenExecutionHandoff, *, now: float) -> GreenExecutionGrant:
        _validate_handoff_shape(handoff)
        try:
            snapshot = self._nodes.require_current(
                node_id=handoff.node_id,
                generation=handoff.node_generation,
                snapshot_hash=handoff.node_snapshot_hash,
                now=now,
            )
        except RoutingError as exc:
            raise ExecutionGateError(exc.reason_code) from exc
        if snapshot.attestation_ref != handoff.node_attestation_ref or snapshot.expires_at != handoff.node_expires_at:
            raise ExecutionGateError("EXECUTION_GATE_NODE_ATTESTATION_MISMATCH")

        try:
            current_policy_hash = self._environment_registry.policy_hash(f"lane:{handoff.lane.value}")
        except EnvironmentContractError as exc:
            raise ExecutionGateError(exc.reason_code) from exc
        if current_policy_hash != handoff.environment_policy_hash:
            raise ExecutionGateError("EXECUTION_GATE_ENV_POLICY_MISMATCH")

        lease = self._scheduler.current(lane=handoff.lane, scope_key=handoff.lease_scope_key)
        if lease is None:
            raise ExecutionGateError("EXECUTION_GATE_LEASE_MISSING_OR_EXPIRED")
        if lease.task_id != handoff.task_id or lease.owner != handoff.node_id:
            raise ExecutionGateError("EXECUTION_GATE_LEASE_OWNER_MISMATCH")
        if lease.fence_token != handoff.fence_token:
            raise ExecutionGateError("EXECUTION_GATE_LEASE_FENCE_MISMATCH")
        if lease.target_state_ref != handoff.envelope.target_state_ref:
            raise ExecutionGateError("EXECUTION_GATE_LEASE_TARGET_MISMATCH")

        if self._ledger.status(handoff.idempotency_key) != "RESERVED":
            raise ExecutionGateError("EXECUTION_GATE_LEDGER_NOT_RESERVED")
        try:
            reservation = self._ledger.reservation_event(handoff.idempotency_key)
            current_fence = self._ledger.current_fence_token(handoff.idempotency_key)
        except LedgerError as exc:
            raise ExecutionGateError(exc.reason_code) from exc
        if reservation.identity.operation_id != handoff.operation_id:
            raise ExecutionGateError("EXECUTION_GATE_LEDGER_OPERATION_MISMATCH")
        if reservation.identity.target_state_ref != handoff.envelope.target_state_ref:
            raise ExecutionGateError("EXECUTION_GATE_LEDGER_TARGET_MISMATCH")
        if current_fence != handoff.fence_token:
            raise ExecutionGateError("EXECUTION_GATE_LEDGER_FENCE_MISMATCH")
        scope_hash = reservation_scope_fingerprint(handoff.envelope)
        if reservation.reservation_scope_hash != scope_hash or handoff.reservation_scope_hash != scope_hash:
            raise ExecutionGateError("EXECUTION_GATE_SCOPE_HASH_MISMATCH")

        try:
            authorization = self._pep.evaluate(handoff.envelope, self._ledger)
        except PEPError as exc:
            raise ExecutionGateError(exc.reason_code) from exc
        if not isinstance(authorization, PEPAuthorizationReceipt):
            raise ExecutionGateError("EXECUTION_GATE_GREEN_AUTHORIZATION_REQUIRED")
        if authorization.authorization_status != handoff.authorization_status:
            raise ExecutionGateError("EXECUTION_GATE_AUTHORIZATION_MISMATCH")

        current_target_ref = self._target_state_verifier.current_ref(handoff)
        if current_target_ref != handoff.envelope.target_state_ref:
            raise ExecutionGateError("EXECUTION_GATE_TARGET_STATE_STALE")

        return GreenExecutionGrant(
            task_id=handoff.task_id,
            node_id=handoff.node_id,
            node_generation=handoff.node_generation,
            node_snapshot_hash=handoff.node_snapshot_hash,
            node_attestation_ref=handoff.node_attestation_ref,
            lane=handoff.lane.value,
            lease_scope_key=handoff.lease_scope_key,
            fence_token=handoff.fence_token,
            environment_policy_hash=handoff.environment_policy_hash,
            operation_id=handoff.operation_id,
            idempotency_key=handoff.idempotency_key,
            adapter_id=handoff.adapter_id,
            target_state_ref=handoff.envelope.target_state_ref,
            reservation_scope_hash=scope_hash,
            planner_envelope_hash=planner_envelope_hash(handoff.envelope),
            policy_reason_code=handoff.policy_reason_code,
            authorization_status=authorization.authorization_status,
            environment=handoff.environment,
            planner_envelope=handoff.envelope,
        )

    @staticmethod
    def public_projection(grant: GreenExecutionGrant) -> dict[str, object]:
        privacy = grant.planner_envelope.privacy.value
        projection: dict[str, object] = {
            "schema": "skeleton.runner_green_execution_grant.v1",
            "privacy": privacy,
            "task_id_hash": _text_hash(grant.task_id),
            "operation_id_hash": _text_hash(grant.operation_id),
            "idempotency_key_hash": _text_hash(grant.idempotency_key),
            "node_id": grant.node_id,
            "node_generation": grant.node_generation,
            "node_snapshot_hash": grant.node_snapshot_hash,
            "node_attestation_ref": grant.node_attestation_ref,
            "lane": grant.lane,
            "fence_token": grant.fence_token,
            "environment_policy_hash": grant.environment_policy_hash,
            "adapter_id": grant.adapter_id,
            "reservation_scope_hash": grant.reservation_scope_hash,
            "planner_envelope_hash": grant.planner_envelope_hash,
            "policy_reason_code": grant.policy_reason_code,
            "authorization_status": grant.authorization_status,
            "execution_authorized": grant.execution_authorized,
            "effect_execution_started": grant.effect_execution_started,
        }
        if privacy == "PUBLIC_SAFE":
            projection.update({
                "task_id": grant.task_id,
                "operation_id": grant.operation_id,
                "idempotency_key": grant.idempotency_key,
            })
        return projection


def planner_envelope_hash(envelope: ExecutionEnvelope) -> str:
    payload = {
        "operation_id": envelope.operation_id,
        "adapter_id": envelope.adapter_id,
        "target_state_ref": envelope.target_state_ref,
        "fence_token": envelope.fence_token,
        "idempotency_key": envelope.idempotency_key,
        "resources": list(envelope.resources),
        "effects": list(envelope.effects),
        "effect_class": envelope.effect_class.value,
        "policy_reason_code": envelope.policy_reason_code,
        "required_capabilities": list(envelope.required_capabilities),
        "privacy": envelope.privacy.value,
        "dry_run": envelope.dry_run,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_handoff_shape(handoff: GreenExecutionHandoff) -> None:
    if not handoff.execution_authorized or not handoff.lease_retained or not handoff.ledger_reserved:
        raise ExecutionGateError("EXECUTION_GATE_AUTHORIZED_HANDOFF_REQUIRED")
    if handoff.side_effects_executed:
        raise ExecutionGateError("EXECUTION_GATE_EFFECT_ALREADY_STARTED")
    if not handoff.envelope.dry_run:
        raise ExecutionGateError("EXECUTION_GATE_IMMUTABLE_PLANNER_ENVELOPE_REQUIRED")


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
