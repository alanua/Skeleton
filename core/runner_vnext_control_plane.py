from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from core.runner_vnext_adapters import AdapterContractError, AdapterPlanner, ExecutionEnvelope
from core.runner_vnext_contracts import EffectClass, OperationIR, PolicyInput, UniversalTask, classify_policy
from core.runner_vnext_environment import ChildEnvironmentPlan, EnvironmentContractError, LaneEnvironmentPolicyRegistry
from core.runner_vnext_leases import Lane, LeaseError
from core.runner_vnext_ledger import LedgerError, OperationIdentity, OperationLedger
from core.runner_vnext_pep import PEPAuthorizationReceipt, PEPError, PolicyEnforcementPoint, reservation_scope_fingerprint
from core.runner_vnext_routing import RoutePlanner, RoutingError
from core.runner_vnext_scheduler import LaneRequest, VNextScheduler


class ControlPlaneError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class GreenExecutionHandoff:
    task_id: str
    node_id: str
    node_generation: int
    node_snapshot_hash: str
    node_attestation_ref: str
    node_expires_at: float
    lane: Lane
    lease_scope_key: str
    fence_token: int
    environment_policy_hash: str
    operation_id: str
    idempotency_key: str
    adapter_id: str
    policy_reason_code: str
    reservation_scope_hash: str
    authorization_status: str
    environment: Mapping[str, str] = field(repr=False)
    envelope: ExecutionEnvelope = field(repr=False)
    lease_retained: bool = True
    ledger_reserved: bool = True
    execution_authorized: bool = True
    side_effects_executed: bool = False


class GreenControlPlane:
    """Prepare one GREEN operation for execution without executing adapter effects."""

    def __init__(
        self,
        *,
        route_planner: RoutePlanner,
        environment_registry: LaneEnvironmentPolicyRegistry,
        scheduler: VNextScheduler,
        adapter_planner: AdapterPlanner,
        ledger: OperationLedger,
        pep: PolicyEnforcementPoint | None = None,
    ) -> None:
        self._route_planner = route_planner
        self._environment_registry = environment_registry
        self._scheduler = scheduler
        self._adapter_planner = adapter_planner
        self._ledger = ledger
        self._pep = pep or PolicyEnforcementPoint()

    def prepare(
        self,
        *,
        task: UniversalTask,
        operation: OperationIR,
        adapter_id: str,
        lane: Lane,
        lease_scope_key: str,
        target_state_ref: str,
        ttl_seconds: float,
        now: float,
        parent_environment: Mapping[str, str],
        injected_environment: Mapping[str, str],
    ) -> GreenExecutionHandoff:
        if not _public_ref(lease_scope_key):
            raise ControlPlaneError("CONTROL_PLANE_LEASE_SCOPE_INVALID")
        if not _public_ref(target_state_ref):
            raise ControlPlaneError("CONTROL_PLANE_TARGET_STATE_REF_INVALID")
        _validate_task_operation(task, operation)
        policy_input = PolicyInput(
            operation=operation,
            reversibility=task.reversibility,
            privacy=task.privacy,
            target_state_ref=target_state_ref,
            operator_boundary_evidence=task.operator_boundary_evidence,
        )
        decision = classify_policy(policy_input)
        if decision.effect_class is not EffectClass.GREEN:
            raise ControlPlaneError("CONTROL_PLANE_PRIVILEGED_PATH_REQUIRED")

        try:
            route = self._route_planner.plan(task=task, adapter_id=adapter_id, lane=lane, now=now)
        except RoutingError as exc:
            raise ControlPlaneError(exc.reason_code) from exc
        if route.status != "ROUTED" or route.selected_node_id is None or route.selected_generation is None:
            raise ControlPlaneError(route.reason_code)

        try:
            env_plan = self._environment_registry.build(
                lane_id=f"lane:{lane.value}",
                parent_environment=parent_environment,
                injected_environment=injected_environment,
            )
        except EnvironmentContractError as exc:
            raise ControlPlaneError(exc.reason_code) from exc

        lease_request = LaneRequest(
            task_id=task.task_id,
            lane=lane,
            owner=route.selected_node_id,
            scope_key=lease_scope_key,
            ttl_seconds=ttl_seconds,
            target_state_ref=target_state_ref,
        )
        try:
            lease = self._scheduler.reserve(lease_request)
        except LeaseError as exc:
            raise ControlPlaneError(exc.reason_code) from exc

        identity = OperationIdentity(operation.operation_id, operation.idempotency_key, target_state_ref)
        existing_status = self._ledger.status(operation.idempotency_key)
        if existing_status in {"COMPLETED", "FAILED"}:
            if lease.status == "ACQUIRED":
                self._safe_release(lease_request, lease.fence_token)
            raise ControlPlaneError("CONTROL_PLANE_OPERATION_TERMINAL")

        reserved_new = existing_status is None
        try:
            envelope = self._adapter_planner.plan(
                adapter_id=adapter_id,
                task=task,
                operation=operation,
                policy_input=policy_input,
                decision=decision,
                target_state_ref=target_state_ref,
                fence_token=lease.fence_token,
            )
            scope_hash = reservation_scope_fingerprint(envelope)
            self._ledger.reserve(
                identity,
                fence_token=lease.fence_token,
                reservation_scope_hash=scope_hash,
            )
            if existing_status == "RESERVED":
                current_fence = self._ledger.current_fence_token(operation.idempotency_key)
                if current_fence != lease.fence_token:
                    if lease.fence_token <= current_fence:
                        raise ControlPlaneError("CONTROL_PLANE_LEDGER_FENCE_NOT_MONOTONIC")
                    self._ledger.rebind_fence(
                        identity,
                        expected_fence_token=current_fence,
                        new_fence_token=lease.fence_token,
                    )
            authorization = self._pep.evaluate(envelope, self._ledger)
            if not isinstance(authorization, PEPAuthorizationReceipt):
                raise ControlPlaneError("CONTROL_PLANE_GREEN_AUTHORIZATION_REQUIRED")
        except (AdapterContractError, LedgerError, PEPError) as exc:
            self._fail_after_lease(
                lease_request=lease_request,
                fence_token=lease.fence_token,
                identity=identity,
                target_state_ref=target_state_ref,
                reserved_new=reserved_new,
                reason_code=getattr(exc, "reason_code", "CONTROL_PLANE_PREPARATION_FAILED"),
                release_lease=lease.status == "ACQUIRED",
            )
            raise ControlPlaneError(getattr(exc, "reason_code", "CONTROL_PLANE_PREPARATION_FAILED")) from exc
        except ControlPlaneError:
            self._fail_after_lease(
                lease_request=lease_request,
                fence_token=lease.fence_token,
                identity=identity,
                target_state_ref=target_state_ref,
                reserved_new=reserved_new,
                reason_code="CONTROL_PLANE_PREPARATION_FAILED",
                release_lease=lease.status == "ACQUIRED",
            )
            raise

        return _handoff(
            task=task,
            route_node_id=route.selected_node_id,
            route_generation=route.selected_generation,
            route_snapshot_hash=route.selected_snapshot_hash,
            route_attestation_ref=route.selected_attestation_ref,
            route_expires_at=route.selected_expires_at,
            lane=lane,
            lease_scope_key=lease_scope_key,
            fence_token=lease.fence_token,
            env_plan=env_plan,
            envelope=envelope,
            authorization=authorization,
            scope_hash=scope_hash,
        )

    def _fail_after_lease(
        self,
        *,
        lease_request: LaneRequest,
        fence_token: int,
        identity: OperationIdentity,
        target_state_ref: str,
        reserved_new: bool,
        reason_code: str,
        release_lease: bool,
    ) -> None:
        if reserved_new and self._ledger.status(identity.idempotency_key) == "RESERVED":
            try:
                current_fence = self._ledger.current_fence_token(identity.idempotency_key)
                self._ledger.finish(
                    identity,
                    fence_token=current_fence,
                    success=False,
                    reason_code="CONTROL_PLANE_PREPARATION_FAILED",
                    touched_resource_refs=(),
                    before_state_ref=target_state_ref,
                    after_state_ref=target_state_ref,
                    validation_status="FAIL",
                )
            except LedgerError:
                pass
        if release_lease:
            self._safe_release(lease_request, fence_token)

    def _safe_release(self, request: LaneRequest, fence_token: int) -> None:
        try:
            self._scheduler.release(request, fence_token=fence_token)
        except LeaseError:
            pass

    @staticmethod
    def public_projection(handoff: GreenExecutionHandoff) -> dict[str, object]:
        return {
            "schema": "skeleton.runner_green_handoff_receipt.v1",
            "task_id": handoff.task_id,
            "node_id": handoff.node_id,
            "node_generation": handoff.node_generation,
            "node_snapshot_hash": handoff.node_snapshot_hash,
            "node_attestation_ref": handoff.node_attestation_ref,
            "node_expires_at": handoff.node_expires_at,
            "lane": handoff.lane.value,
            "fence_token": handoff.fence_token,
            "environment_policy_hash": handoff.environment_policy_hash,
            "operation_id": handoff.operation_id,
            "idempotency_key": handoff.idempotency_key,
            "adapter_id": handoff.adapter_id,
            "policy_reason_code": handoff.policy_reason_code,
            "reservation_scope_hash": handoff.reservation_scope_hash,
            "authorization_status": handoff.authorization_status,
            "lease_retained": handoff.lease_retained,
            "ledger_reserved": handoff.ledger_reserved,
            "execution_authorized": handoff.execution_authorized,
            "side_effects_executed": handoff.side_effects_executed,
        }


def _handoff(
    *,
    task: UniversalTask,
    route_node_id: str,
    route_generation: int,
    route_snapshot_hash: str | None,
    route_attestation_ref: str | None,
    route_expires_at: float | None,
    lane: Lane,
    lease_scope_key: str,
    fence_token: int,
    env_plan: ChildEnvironmentPlan,
    envelope: ExecutionEnvelope,
    authorization: PEPAuthorizationReceipt,
    scope_hash: str,
) -> GreenExecutionHandoff:
    if route_snapshot_hash is None or route_attestation_ref is None or route_expires_at is None:
        raise ControlPlaneError("CONTROL_PLANE_ROUTE_SNAPSHOT_BINDING_REQUIRED")
    return GreenExecutionHandoff(
        task_id=task.task_id,
        node_id=route_node_id,
        node_generation=route_generation,
        node_snapshot_hash=route_snapshot_hash,
        node_attestation_ref=route_attestation_ref,
        node_expires_at=route_expires_at,
        lane=lane,
        lease_scope_key=lease_scope_key,
        fence_token=fence_token,
        environment_policy_hash=env_plan.receipt.policy_hash,
        operation_id=envelope.operation_id,
        idempotency_key=envelope.idempotency_key,
        adapter_id=envelope.adapter_id,
        policy_reason_code=envelope.policy_reason_code,
        reservation_scope_hash=scope_hash,
        authorization_status=authorization.authorization_status,
        environment=env_plan.environment,
        envelope=envelope,
    )


def _validate_task_operation(task: UniversalTask, operation: OperationIR) -> None:
    if not _public_ref(operation.operation_id) or not operation.operation_id.startswith("op:"):
        raise ControlPlaneError("CONTROL_PLANE_OPERATION_ID_INVALID")
    if not _public_ref(task.idempotency_key) or not task.idempotency_key.startswith("idem:"):
        raise ControlPlaneError("CONTROL_PLANE_IDEMPOTENCY_KEY_INVALID")
    if task.idempotency_key != operation.idempotency_key:
        raise ControlPlaneError("CONTROL_PLANE_TASK_OPERATION_IDEMPOTENCY_MISMATCH")
    if task.target_resources != operation.resources:
        raise ControlPlaneError("CONTROL_PLANE_TASK_OPERATION_RESOURCE_MISMATCH")
    if task.expected_effects != operation.effects:
        raise ControlPlaneError("CONTROL_PLANE_TASK_OPERATION_EFFECT_MISMATCH")


def _public_ref(value: str) -> bool:
    if not value or "\\" in value or ".." in value or any(ch.isspace() for ch in value):
        return False
    namespace, sep, tail = value.partition(":")
    return bool(sep and namespace and tail) and not tail.startswith(("/", "~"))
