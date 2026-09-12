from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from core.runner_vnext_authority import (
    BoundRunnerOperation,
    RunnerVNextAuthorityError,
    RunnerVNextStores,
    authorize_privileged_broker_request,
    grant_green_authority,
    prepare_green_authority,
    reserve_privileged_authority,
)
from core.runner_vnext_contracts import EffectClass
from core.runner_vnext_execution import (
    GreenAdapterExecutor,
    GreenExecutionError,
    GreenExecutionLifecycle,
    GreenExecutionReceipt,
    GreenExecutionResult,
)
from core.runner_vnext_execution_gate import GreenExecutionGrant, TargetStateVerifier
from core.runner_vnext_leases import LeaseError
from core.runner_vnext_ledger import LedgerEvent, LedgerError, OperationIdentity
from core.runner_vnext_pep import AuthorityClaim, AuthorityVerifier, PrivilegedBrokerRequest
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot, RoutePlanner
from core.runner_vnext_scheduler import LaneRequest


class RunnerVNextDispatchError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class MechanicalResult:
    succeeded: bool
    reason_code: str
    touched_resources: tuple[str, ...]
    before_state_ref: str
    after_state_ref: str
    validation_status: str
    rollback_requested: bool = False


@dataclass(frozen=True)
class PrivilegedExecutionGrant:
    adapter_id: str
    operation_id: str
    target_state_ref: str
    fence_token: int
    idempotency_key: str
    resources: tuple[str, ...]
    effects: tuple[str, ...]
    effect_class: EffectClass
    policy_reason_code: str
    approval_evidence_refs: tuple[str, ...]
    execution_authorized: bool = True


class GreenMechanicalBackend(Protocol):
    def execute(
        self,
        *,
        bound: BoundRunnerOperation,
        grant: GreenExecutionGrant,
    ) -> MechanicalResult: ...


class PrivilegedMechanicalBackend(Protocol):
    def execute(
        self,
        *,
        bound: BoundRunnerOperation,
        grant: PrivilegedExecutionGrant,
    ) -> MechanicalResult: ...


@dataclass(frozen=True)
class AuthoritativeDispatchReceipt:
    operation_id: str
    idempotency_key: str
    route: str
    operation: str
    lane: str
    adapter_id: str
    terminal_status: str
    reason_code: str
    validation_status: str
    touched_resources: tuple[str, ...]
    before_state_ref: str
    after_state_ref: str
    replayed: bool
    execution_started: bool
    lease_released: bool

    def to_public_mapping(self) -> dict[str, object]:
        return {
            "schema": "skeleton.runner_vnext_authoritative_dispatch_receipt.v1",
            "operation_id_hash": _hash_text(self.operation_id),
            "idempotency_key_hash": _hash_text(self.idempotency_key),
            "route": self.route,
            "operation": self.operation,
            "lane": self.lane,
            "adapter_id": self.adapter_id,
            "terminal_status": self.terminal_status,
            "reason_code": self.reason_code,
            "validation_status": self.validation_status,
            "touched_resource_count": len(self.touched_resources),
            "touched_resource_hash": _hash_refs(self.touched_resources),
            "before_state_ref_hash": _hash_text(self.before_state_ref),
            "after_state_ref_hash": _hash_text(self.after_state_ref),
            "replayed": self.replayed,
            "execution_started": self.execution_started,
            "lease_released": self.lease_released,
        }


class ExactAuthorityVerifier(AuthorityVerifier):
    def __init__(self, expected_evidence_refs: tuple[str, ...]) -> None:
        self._expected = expected_evidence_refs

    def verify(self, claim: AuthorityClaim) -> bool:
        return claim.evidence_refs == self._expected


class _GreenMechanicalAdapter(GreenAdapterExecutor):
    def __init__(
        self,
        *,
        bound: BoundRunnerOperation,
        backend: GreenMechanicalBackend,
    ) -> None:
        self._bound = bound
        self._backend = backend

    def execute(self, grant: GreenExecutionGrant) -> GreenExecutionResult:
        _validate_green_grant(self._bound, grant)
        result = self._backend.execute(bound=self._bound, grant=grant)
        return GreenExecutionResult(
            operation_id=grant.operation_id,
            idempotency_key=grant.idempotency_key,
            adapter_id=grant.adapter_id,
            target_state_ref=grant.target_state_ref,
            fence_token=grant.fence_token,
            resources=result.touched_resources,
            effects=grant.planner_envelope.effects,
            outcome="SUCCEEDED" if result.succeeded else "FAILED",
            reason_code=result.reason_code,
            before_state_ref=result.before_state_ref,
            after_state_ref=result.after_state_ref,
            validation_status=result.validation_status,
            rollback_requested=result.rollback_requested,
        )


def run_green_authoritative_dispatch(
    *,
    bound: BoundRunnerOperation,
    node_snapshot: NodeCapabilitySnapshot,
    stores: RunnerVNextStores,
    target_state_verifier: TargetStateVerifier,
    backend: GreenMechanicalBackend,
    now: float,
    ttl_seconds: float,
    parent_environment: dict[str, str],
) -> AuthoritativeDispatchReceipt:
    if bound.policy_decision.effect_class is not EffectClass.GREEN:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_GREEN_POLICY_REQUIRED")
    replay = _terminal_replay(bound, stores)
    if replay is not None:
        return replay
    _fail_closed_on_started(bound, stores)
    nodes = _exact_node_registry(bound, node_snapshot, now=now)
    try:
        handoff = prepare_green_authority(
            bound=bound,
            nodes=nodes,
            stores=stores,
            ttl_seconds=ttl_seconds,
            now=now,
            parent_environment=parent_environment,
        )
        grant = grant_green_authority(
            handoff=handoff,
            nodes=nodes,
            stores=stores,
            target_state_verifier=target_state_verifier,
            now=now,
        )
        execution = GreenExecutionLifecycle(
            scheduler=stores.scheduler,
            ledger=stores.ledger,
        ).run(
            grant,
            _GreenMechanicalAdapter(bound=bound, backend=backend),
        )
    except (RunnerVNextAuthorityError, GreenExecutionError, LedgerError, LeaseError) as exc:
        raise RunnerVNextDispatchError(
            getattr(exc, "reason_code", "VNEXT_DISPATCH_GREEN_FAILED")
        ) from exc
    return _from_green_receipt(bound, execution)


def run_privileged_authoritative_dispatch(
    *,
    bound: BoundRunnerOperation,
    node_snapshot: NodeCapabilitySnapshot,
    stores: RunnerVNextStores,
    target_state_verifier: TargetStateVerifier,
    backend: PrivilegedMechanicalBackend,
    evidence_refs: tuple[str, ...],
    fresh_authority: bool,
    now: float,
    ttl_seconds: float,
) -> AuthoritativeDispatchReceipt:
    if bound.policy_decision.effect_class is EffectClass.GREEN:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_PRIVILEGED_POLICY_REQUIRED")
    replay = _terminal_replay(bound, stores)
    if replay is not None:
        return replay
    _fail_closed_on_started(bound, stores)
    nodes = _exact_node_registry(bound, node_snapshot, now=now)
    plan = RoutePlanner(nodes).plan(
        task=bound.universal_task,
        adapter_id=bound.binding.adapter_id,
        lane=bound.binding.lane,
        now=now,
    )
    if plan.status != "ROUTED" or plan.selected_node_id != node_snapshot.node_id:
        raise RunnerVNextDispatchError(plan.reason_code)
    if target_state_verifier.current_ref(bound) != bound.target_state_ref:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_TARGET_STATE_STALE")
    try:
        lease = stores.scheduler.reserve(
            LaneRequest(
                task_id=bound.universal_task.task_id,
                lane=bound.binding.lane,
                owner=node_snapshot.node_id,
                scope_key=bound.lease_scope_key,
                ttl_seconds=ttl_seconds,
                target_state_ref=bound.target_state_ref,
            )
        )
        envelope = reserve_privileged_authority(
            bound=bound,
            ledger=stores.ledger,
            fence_token=lease.fence_token,
        )
        claim = AuthorityClaim(
            evidence_refs=evidence_refs,
            operation_id=envelope.operation_id,
            target_state_ref=envelope.target_state_ref,
            effect_class=envelope.effect_class,
            resources=envelope.resources,
            fresh=fresh_authority,
        )
        broker_request = authorize_privileged_broker_request(
            envelope=envelope,
            ledger=stores.ledger,
            authority_claim=claim,
            authority_verifier=ExactAuthorityVerifier(evidence_refs),
        )
        grant = _privileged_execution_grant(broker_request, evidence_refs=evidence_refs)
        if target_state_verifier.current_ref(bound) != bound.target_state_ref:
            raise RunnerVNextDispatchError("VNEXT_DISPATCH_TARGET_STATE_STALE")
        identity = OperationIdentity(
            bound.operation_ir.operation_id,
            bound.operation_ir.idempotency_key,
            bound.target_state_ref,
        )
        stores.ledger.mark_started(
            identity,
            fence_token=lease.fence_token,
            execution_grant_hash=_privileged_grant_hash(grant),
        )
        result = backend.execute(bound=bound, grant=grant)
        _validate_privileged_mechanical_result(bound, grant, result)
        stores.ledger.finish(
            identity,
            fence_token=lease.fence_token,
            success=result.succeeded,
            reason_code=result.reason_code,
            touched_resource_refs=result.touched_resources,
            before_state_ref=result.before_state_ref,
            after_state_ref=result.after_state_ref,
            validation_status=result.validation_status,
        )
        released = stores.scheduler.release_exact(
            lane=bound.binding.lane,
            scope_key=bound.lease_scope_key,
            owner=node_snapshot.node_id,
            fence_token=lease.fence_token,
        )
    except RunnerVNextDispatchError:
        raise
    except (RunnerVNextAuthorityError, LedgerError, LeaseError) as exc:
        raise RunnerVNextDispatchError(
            getattr(exc, "reason_code", "VNEXT_DISPATCH_PRIVILEGED_FAILED")
        ) from exc
    terminal = "COMPLETED" if result.succeeded else "FAILED"
    return AuthoritativeDispatchReceipt(
        operation_id=bound.operation_ir.operation_id,
        idempotency_key=bound.operation_ir.idempotency_key,
        route=bound.route,
        operation=bound.operation,
        lane=bound.binding.lane.value,
        adapter_id=bound.binding.adapter_id,
        terminal_status=terminal,
        reason_code=result.reason_code,
        validation_status=result.validation_status,
        touched_resources=result.touched_resources,
        before_state_ref=result.before_state_ref,
        after_state_ref=result.after_state_ref,
        replayed=False,
        execution_started=True,
        lease_released=released.status == "RELEASED",
    )


def _privileged_execution_grant(
    request: PrivilegedBrokerRequest,
    *,
    evidence_refs: tuple[str, ...],
) -> PrivilegedExecutionGrant:
    if request.execution_authorized:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_BROKER_REQUEST_STATE_INVALID")
    if request.approval_evidence_refs != evidence_refs or not evidence_refs:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_BROKER_EVIDENCE_MISMATCH")
    return PrivilegedExecutionGrant(
        adapter_id=request.adapter_id,
        operation_id=request.operation_id,
        target_state_ref=request.target_state_ref,
        fence_token=request.fence_token,
        idempotency_key=request.idempotency_key,
        resources=request.resources,
        effects=request.effects,
        effect_class=request.effect_class,
        policy_reason_code=request.policy_reason_code,
        approval_evidence_refs=request.approval_evidence_refs,
    )


def _validate_privileged_mechanical_result(
    bound: BoundRunnerOperation,
    grant: PrivilegedExecutionGrant,
    result: MechanicalResult,
) -> None:
    if not grant.execution_authorized:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_PRIVILEGED_GRANT_REQUIRED")
    if result.validation_status not in {"PASS", "FAIL"}:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_RESULT_VALIDATION_INVALID")
    if result.succeeded != (result.validation_status == "PASS"):
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_RESULT_VALIDATION_MISMATCH")
    if not result.reason_code or any(
        ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in result.reason_code
    ):
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_RESULT_REASON_INVALID")
    if result.before_state_ref != bound.target_state_ref:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_RESULT_BEFORE_STATE_MISMATCH")
    if not _public_ref(result.before_state_ref) or not _public_ref(result.after_state_ref):
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_RESULT_STATE_REF_INVALID")
    if len(set(result.touched_resources)) != len(result.touched_resources):
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_RESULT_RESOURCE_DUPLICATE")
    authorized = set(grant.resources)
    if any(not _public_ref(resource) or resource not in authorized for resource in result.touched_resources):
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_RESULT_RESOURCE_OUT_OF_SCOPE")
    if result.succeeded and not result.touched_resources:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_RESULT_RESOURCE_REQUIRED")
    if (
        not result.succeeded
        and not result.touched_resources
        and (result.before_state_ref != result.after_state_ref or result.rollback_requested)
    ):
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_FAILED_MUTATION_RESOURCE_REQUIRED")


def _exact_node_registry(
    bound: BoundRunnerOperation,
    snapshot: NodeCapabilitySnapshot,
    *,
    now: float,
) -> NodeCapabilityRegistry:
    expected = (
        tuple(bound.universal_task.required_capabilities),
        (bound.binding.adapter_id,),
        (bound.binding.lane,),
        (bound.universal_task.privacy,),
        tuple(bound.universal_task.target_resources),
    )
    actual = (
        snapshot.capabilities,
        snapshot.supported_adapters,
        snapshot.supported_lanes,
        snapshot.privacy_classes,
        snapshot.resource_patterns,
    )
    if actual != expected:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_NODE_EVIDENCE_SCOPE_MISMATCH")
    if not snapshot.attestation_ref.startswith("attestation:"):
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_NODE_ATTESTATION_REQUIRED")
    registry = NodeCapabilityRegistry()
    try:
        registry.register(snapshot, now=now)
    except Exception as exc:
        raise RunnerVNextDispatchError(
            getattr(exc, "reason_code", "VNEXT_DISPATCH_NODE_EVIDENCE_INVALID")
        ) from exc
    return registry


def _terminal_replay(
    bound: BoundRunnerOperation,
    stores: RunnerVNextStores,
) -> AuthoritativeDispatchReceipt | None:
    status = stores.ledger.status(bound.operation_ir.idempotency_key)
    if status not in {"COMPLETED", "FAILED"}:
        return None
    events = stores.ledger.history(bound.operation_ir.idempotency_key)
    terminal = next(
        event for event in reversed(events) if event.event_type in {"COMPLETED", "FAILED"}
    )
    _validate_terminal_identity(bound, terminal)
    return AuthoritativeDispatchReceipt(
        operation_id=terminal.identity.operation_id,
        idempotency_key=terminal.identity.idempotency_key,
        route=bound.route,
        operation=bound.operation,
        lane=bound.binding.lane.value,
        adapter_id=bound.binding.adapter_id,
        terminal_status=terminal.event_type,
        reason_code=terminal.reason_code,
        validation_status=terminal.validation_status or "FAIL",
        touched_resources=terminal.touched_resource_refs,
        before_state_ref=terminal.before_state_ref or bound.target_state_ref,
        after_state_ref=terminal.after_state_ref or bound.target_state_ref,
        replayed=True,
        execution_started=True,
        lease_released=True,
    )


def _fail_closed_on_started(bound: BoundRunnerOperation, stores: RunnerVNextStores) -> None:
    status = stores.ledger.status(bound.operation_ir.idempotency_key)
    if status == "STARTED":
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_STARTED_NEEDS_RECOVERY")


def _validate_terminal_identity(bound: BoundRunnerOperation, event: LedgerEvent) -> None:
    if (
        event.identity.operation_id != bound.operation_ir.operation_id
        or event.identity.idempotency_key != bound.operation_ir.idempotency_key
        or event.identity.target_state_ref != bound.target_state_ref
    ):
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_TERMINAL_IDENTITY_MISMATCH")


def _validate_green_grant(bound: BoundRunnerOperation, grant: GreenExecutionGrant) -> None:
    expected = (
        bound.universal_task.task_id,
        bound.operation_ir.operation_id,
        bound.operation_ir.idempotency_key,
        bound.binding.adapter_id,
        bound.target_state_ref,
        bound.operation_ir.resources,
        bound.operation_ir.effects,
        bound.universal_task.required_capabilities,
    )
    actual = (
        grant.task_id,
        grant.operation_id,
        grant.idempotency_key,
        grant.adapter_id,
        grant.target_state_ref,
        grant.planner_envelope.resources,
        grant.planner_envelope.effects,
        grant.planner_envelope.required_capabilities,
    )
    if actual != expected:
        raise RunnerVNextDispatchError("VNEXT_DISPATCH_GRANT_SCOPE_MISMATCH")


def _from_green_receipt(
    bound: BoundRunnerOperation,
    receipt: GreenExecutionReceipt,
) -> AuthoritativeDispatchReceipt:
    return AuthoritativeDispatchReceipt(
        operation_id=receipt.operation_id,
        idempotency_key=receipt.idempotency_key,
        route=bound.route,
        operation=bound.operation,
        lane=receipt.lane,
        adapter_id=receipt.adapter_id,
        terminal_status=receipt.terminal_status,
        reason_code=receipt.reason_code,
        validation_status=receipt.validation_status,
        touched_resources=receipt.touched_resource_refs,
        before_state_ref=receipt.before_state_ref,
        after_state_ref=receipt.after_state_ref,
        replayed=False,
        execution_started=receipt.effect_execution_started,
        lease_released=receipt.lease_released,
    )


def _privileged_grant_hash(grant: PrivilegedExecutionGrant) -> str:
    payload = {
        "adapter_id": grant.adapter_id,
        "operation_id": grant.operation_id,
        "target_state_ref": grant.target_state_ref,
        "fence_token": grant.fence_token,
        "idempotency_key": grant.idempotency_key,
        "resources": list(grant.resources),
        "effects": list(grant.effects),
        "effect_class": grant.effect_class.value,
        "policy_reason_code": grant.policy_reason_code,
        "approval_evidence_refs": list(grant.approval_evidence_refs),
        "execution_authorized": grant.execution_authorized,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _public_ref(value: str) -> bool:
    return (
        bool(value)
        and not value.startswith(("/", "~"))
        and "\\" not in value
        and ".." not in value
        and ":" in value
        and not any(ch.isspace() for ch in value)
    )


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_refs(values: tuple[str, ...]) -> str:
    encoded = json.dumps(sorted(values), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
