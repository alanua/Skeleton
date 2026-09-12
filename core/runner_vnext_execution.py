from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Protocol

from core.runner_vnext_execution_gate import GreenExecutionGrant, planner_envelope_hash
from core.runner_vnext_leases import Lane, LeaseError
from core.runner_vnext_ledger import LedgerError, OperationIdentity, OperationLedger
from core.runner_vnext_pep import reservation_scope_fingerprint
from core.runner_vnext_scheduler import VNextScheduler


class GreenExecutionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class GreenExecutionResult:
    operation_id: str
    idempotency_key: str
    adapter_id: str
    target_state_ref: str
    fence_token: int
    resources: tuple[str, ...]
    effects: tuple[str, ...]
    outcome: str
    reason_code: str
    before_state_ref: str
    after_state_ref: str
    validation_status: str
    rollback_requested: bool = False


class GreenAdapterExecutor(Protocol):
    def execute(self, grant: GreenExecutionGrant) -> GreenExecutionResult: ...


@dataclass(frozen=True)
class GreenExecutionStarted:
    operation_id: str
    idempotency_key: str
    adapter_id: str
    fence_token: int
    planner_envelope_hash: str
    execution_grant_hash: str
    effect_execution_started: bool = True


@dataclass(frozen=True)
class GreenExecutionReceipt:
    task_id: str
    node_id: str
    lane: str
    lease_scope_key: str = field(repr=False)
    fence_token: int
    operation_id: str
    idempotency_key: str
    adapter_id: str
    planner_envelope_hash: str
    execution_grant_hash: str
    privacy: str
    terminal_status: str
    reason_code: str
    validation_status: str
    touched_resource_refs: tuple[str, ...]
    before_state_ref: str
    after_state_ref: str
    rollback_required: bool
    lease_released: bool
    needs_recovery: bool
    effect_execution_started: bool = True


class GreenExecutionLifecycle:
    """Typed GREEN effect lifecycle. No generic shell/process implementation lives here."""

    def __init__(self, *, scheduler: VNextScheduler, ledger: OperationLedger) -> None:
        self._scheduler = scheduler
        self._ledger = ledger

    def run(self, grant: GreenExecutionGrant, executor: GreenAdapterExecutor) -> GreenExecutionReceipt:
        started = self.begin(grant)
        try:
            result = executor.execute(grant)
        except Exception as exc:
            raise GreenExecutionError("EXECUTION_ADAPTER_EXCEPTION_NEEDS_RECOVERY") from exc
        return self.finalize(grant, started=started, result=result)

    def begin(self, grant: GreenExecutionGrant) -> GreenExecutionStarted:
        _validate_grant(grant)
        self._validate_authoritative_state(grant, expected_status="RESERVED")
        identity = OperationIdentity(grant.operation_id, grant.idempotency_key, grant.target_state_ref)
        grant_hash = execution_grant_hash(grant)
        try:
            self._ledger.mark_started(identity, fence_token=grant.fence_token, execution_grant_hash=grant_hash)
        except LedgerError as exc:
            raise GreenExecutionError(exc.reason_code) from exc
        return GreenExecutionStarted(
            operation_id=grant.operation_id,
            idempotency_key=grant.idempotency_key,
            adapter_id=grant.adapter_id,
            fence_token=grant.fence_token,
            planner_envelope_hash=grant.planner_envelope_hash,
            execution_grant_hash=grant_hash,
        )

    def finalize(
        self,
        grant: GreenExecutionGrant,
        *,
        started: GreenExecutionStarted,
        result: GreenExecutionResult,
    ) -> GreenExecutionReceipt:
        _validate_started(grant, started)
        self._validate_authoritative_state(grant, expected_status="STARTED", execution_grant_hash=started.execution_grant_hash)
        _validate_result(grant, result)

        success = result.outcome == "SUCCEEDED"
        if success and result.validation_status != "PASS":
            raise GreenExecutionError("EXECUTION_SUCCESS_REQUIRES_VALIDATION_PASS")
        if not success and result.validation_status != "FAIL":
            raise GreenExecutionError("EXECUTION_FAILURE_REQUIRES_VALIDATION_FAIL")

        identity = OperationIdentity(grant.operation_id, grant.idempotency_key, grant.target_state_ref)
        try:
            self._ledger.finish(
                identity,
                fence_token=grant.fence_token,
                success=success,
                reason_code=result.reason_code,
                touched_resource_refs=result.resources,
                before_state_ref=result.before_state_ref,
                after_state_ref=result.after_state_ref,
                validation_status=result.validation_status,
            )
        except LedgerError as exc:
            raise GreenExecutionError(exc.reason_code) from exc

        lease_released = self._release_exact_lease(grant)
        state_changed = result.before_state_ref != result.after_state_ref
        rollback_required = (not success) and (result.rollback_requested or state_changed or result.outcome == "PARTIAL")
        needs_recovery = rollback_required or not lease_released
        terminal_status = "COMPLETED" if success else "FAILED"
        return GreenExecutionReceipt(
            task_id=grant.task_id,
            node_id=grant.node_id,
            lane=grant.lane,
            lease_scope_key=grant.lease_scope_key,
            fence_token=grant.fence_token,
            operation_id=grant.operation_id,
            idempotency_key=grant.idempotency_key,
            adapter_id=grant.adapter_id,
            planner_envelope_hash=grant.planner_envelope_hash,
            execution_grant_hash=started.execution_grant_hash,
            privacy=grant.planner_envelope.privacy.value,
            terminal_status=terminal_status,
            reason_code=result.reason_code,
            validation_status=result.validation_status,
            touched_resource_refs=result.resources,
            before_state_ref=result.before_state_ref,
            after_state_ref=result.after_state_ref,
            rollback_required=rollback_required,
            lease_released=lease_released,
            needs_recovery=needs_recovery,
        )

    def _validate_authoritative_state(
        self,
        grant: GreenExecutionGrant,
        *,
        expected_status: str,
        execution_grant_hash: str | None = None,
    ) -> None:
        status = self._ledger.status(grant.idempotency_key)
        if status != expected_status:
            reason = "EXECUTION_ALREADY_STARTED_NEEDS_RECOVERY" if status == "STARTED" and expected_status == "RESERVED" else "EXECUTION_LEDGER_STATE_MISMATCH"
            raise GreenExecutionError(reason)
        try:
            reservation = self._ledger.reservation_event(grant.idempotency_key)
            current_fence = self._ledger.current_fence_token(grant.idempotency_key)
        except LedgerError as exc:
            raise GreenExecutionError(exc.reason_code) from exc
        if reservation.identity.operation_id != grant.operation_id or reservation.identity.target_state_ref != grant.target_state_ref:
            raise GreenExecutionError("EXECUTION_LEDGER_IDENTITY_MISMATCH")
        if current_fence != grant.fence_token:
            raise GreenExecutionError("EXECUTION_LEDGER_FENCE_MISMATCH")
        if reservation.reservation_scope_hash != reservation_scope_fingerprint(grant.planner_envelope):
            raise GreenExecutionError("EXECUTION_LEDGER_SCOPE_MISMATCH")
        if expected_status == "STARTED":
            try:
                started = self._ledger.started_event(grant.idempotency_key)
            except LedgerError as exc:
                raise GreenExecutionError(exc.reason_code) from exc
            if started.identity != reservation.identity or started.fence_token != grant.fence_token:
                raise GreenExecutionError("EXECUTION_START_EVIDENCE_MISMATCH")
            if execution_grant_hash is None or started.execution_grant_hash != execution_grant_hash:
                raise GreenExecutionError("EXECUTION_START_GRANT_HASH_MISMATCH")
            if started.reservation_scope_hash != reservation.reservation_scope_hash:
                raise GreenExecutionError("EXECUTION_START_SCOPE_MISMATCH")

        try:
            lane = Lane(grant.lane)
        except ValueError as exc:
            raise GreenExecutionError("EXECUTION_LANE_INVALID") from exc
        lease = self._scheduler.current(lane=lane, scope_key=grant.lease_scope_key)
        if lease is None:
            raise GreenExecutionError("EXECUTION_LEASE_MISSING_OR_EXPIRED")
        if lease.task_id != grant.task_id or lease.owner != grant.node_id:
            raise GreenExecutionError("EXECUTION_LEASE_OWNER_MISMATCH")
        if lease.fence_token != grant.fence_token:
            raise GreenExecutionError("EXECUTION_LEASE_FENCE_MISMATCH")
        if lease.target_state_ref != grant.target_state_ref:
            raise GreenExecutionError("EXECUTION_LEASE_TARGET_MISMATCH")

    def _release_exact_lease(self, grant: GreenExecutionGrant) -> bool:
        try:
            self._scheduler.release_exact(
                lane=Lane(grant.lane),
                scope_key=grant.lease_scope_key,
                owner=grant.node_id,
                fence_token=grant.fence_token,
            )
            return True
        except LeaseError:
            return False

    @staticmethod
    def public_projection(receipt: GreenExecutionReceipt) -> dict[str, object]:
        projection: dict[str, object] = {
            "schema": "skeleton.runner_green_execution_receipt.v1",
            "task_id_hash": _text_hash(receipt.task_id),
            "operation_id_hash": _text_hash(receipt.operation_id),
            "idempotency_key_hash": _text_hash(receipt.idempotency_key),
            "node_id": receipt.node_id,
            "lane": receipt.lane,
            "fence_token": receipt.fence_token,
            "adapter_id": receipt.adapter_id,
            "planner_envelope_hash": receipt.planner_envelope_hash,
            "execution_grant_hash": receipt.execution_grant_hash,
            "privacy": receipt.privacy,
            "terminal_status": receipt.terminal_status,
            "reason_code": receipt.reason_code,
            "validation_status": receipt.validation_status,
            "touched_resource_count": len(receipt.touched_resource_refs),
            "touched_resource_hash": _refs_hash(receipt.touched_resource_refs),
            "before_state_ref_hash": _text_hash(receipt.before_state_ref),
            "after_state_ref_hash": _text_hash(receipt.after_state_ref),
            "state_changed": receipt.before_state_ref != receipt.after_state_ref,
            "rollback_required": receipt.rollback_required,
            "lease_released": receipt.lease_released,
            "needs_recovery": receipt.needs_recovery,
            "effect_execution_started": receipt.effect_execution_started,
        }
        if receipt.privacy == "PUBLIC_SAFE":
            projection.update({
                "task_id": receipt.task_id,
                "operation_id": receipt.operation_id,
                "idempotency_key": receipt.idempotency_key,
                "touched_resource_refs": list(receipt.touched_resource_refs),
                "before_state_ref": receipt.before_state_ref,
                "after_state_ref": receipt.after_state_ref,
            })
        return projection


def _validate_grant(grant: GreenExecutionGrant) -> None:
    if not grant.execution_authorized or grant.effect_execution_started:
        raise GreenExecutionError("EXECUTION_FRESH_AUTHORIZED_GRANT_REQUIRED")
    if grant.planner_envelope_hash != planner_envelope_hash(grant.planner_envelope):
        raise GreenExecutionError("EXECUTION_PLANNER_ENVELOPE_HASH_MISMATCH")
    if grant.planner_envelope.operation_id != grant.operation_id or grant.planner_envelope.idempotency_key != grant.idempotency_key:
        raise GreenExecutionError("EXECUTION_GRANT_ENVELOPE_IDENTITY_MISMATCH")
    if grant.planner_envelope.adapter_id != grant.adapter_id or grant.planner_envelope.target_state_ref != grant.target_state_ref:
        raise GreenExecutionError("EXECUTION_GRANT_ENVELOPE_BINDING_MISMATCH")
    if grant.planner_envelope.fence_token != grant.fence_token:
        raise GreenExecutionError("EXECUTION_GRANT_ENVELOPE_FENCE_MISMATCH")
    if not _public_ref(grant.lease_scope_key):
        raise GreenExecutionError("EXECUTION_LEASE_SCOPE_INVALID")


def _validate_started(grant: GreenExecutionGrant, started: GreenExecutionStarted) -> None:
    if not started.effect_execution_started:
        raise GreenExecutionError("EXECUTION_STARTED_MARKER_REQUIRED")
    expected = (grant.operation_id, grant.idempotency_key, grant.adapter_id, grant.fence_token, grant.planner_envelope_hash, execution_grant_hash(grant))
    actual = (started.operation_id, started.idempotency_key, started.adapter_id, started.fence_token, started.planner_envelope_hash, started.execution_grant_hash)
    if actual != expected:
        raise GreenExecutionError("EXECUTION_STARTED_MARKER_MISMATCH")


def _validate_result(grant: GreenExecutionGrant, result: GreenExecutionResult) -> None:
    if result.outcome not in {"SUCCEEDED", "FAILED", "PARTIAL"}:
        raise GreenExecutionError("EXECUTION_RESULT_OUTCOME_INVALID")
    if result.validation_status not in {"PASS", "FAIL"}:
        raise GreenExecutionError("EXECUTION_RESULT_VALIDATION_INVALID")
    if not result.reason_code or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in result.reason_code):
        raise GreenExecutionError("EXECUTION_RESULT_REASON_INVALID")
    expected = (grant.operation_id, grant.idempotency_key, grant.adapter_id, grant.target_state_ref, grant.fence_token, grant.planner_envelope.effects)
    actual = (result.operation_id, result.idempotency_key, result.adapter_id, result.target_state_ref, result.fence_token, result.effects)
    if actual != expected:
        raise GreenExecutionError("EXECUTION_RESULT_BINDING_MISMATCH_NEEDS_RECOVERY")
    if len(set(result.resources)) != len(result.resources):
        raise GreenExecutionError("EXECUTION_RESULT_RESOURCE_SCOPE_INVALID_NEEDS_RECOVERY")
    authorized = frozenset(grant.planner_envelope.resources)
    for ref in (*result.resources, result.before_state_ref, result.after_state_ref):
        if not _public_ref(ref):
            raise GreenExecutionError("EXECUTION_RESULT_PUBLIC_REF_REQUIRED")
    if any(ref not in authorized for ref in result.resources):
        raise GreenExecutionError("EXECUTION_RESULT_RESOURCE_SCOPE_INVALID_NEEDS_RECOVERY")
    if result.outcome == "SUCCEEDED" and result.before_state_ref != result.after_state_ref and not result.resources:
        raise GreenExecutionError("EXECUTION_SUCCESS_REQUIRES_TOUCHED_RESOURCES")


def execution_grant_hash(grant: GreenExecutionGrant) -> str:
    payload = {
        "task_id": grant.task_id,
        "node_id": grant.node_id,
        "node_generation": grant.node_generation,
        "node_snapshot_hash": grant.node_snapshot_hash,
        "node_attestation_ref": grant.node_attestation_ref,
        "lane": grant.lane,
        "lease_scope_key": grant.lease_scope_key,
        "fence_token": grant.fence_token,
        "environment_policy_hash": grant.environment_policy_hash,
        "operation_id": grant.operation_id,
        "idempotency_key": grant.idempotency_key,
        "adapter_id": grant.adapter_id,
        "target_state_ref": grant.target_state_ref,
        "reservation_scope_hash": grant.reservation_scope_hash,
        "planner_envelope_hash": grant.planner_envelope_hash,
        "policy_reason_code": grant.policy_reason_code,
        "authorization_status": grant.authorization_status,
        "execution_authorized": grant.execution_authorized,
        "effect_execution_started": grant.effect_execution_started,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _refs_hash(values: tuple[str, ...]) -> str:
    payload = json.dumps(sorted(values), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _public_ref(value: str) -> bool:
    return bool(value) and not value.startswith(("/", "~")) and "\\" not in value and ".." not in value and ":" in value and not any(ch.isspace() for ch in value)
