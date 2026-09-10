from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Protocol

from core.runner_vnext_execution import execution_grant_hash
from core.runner_vnext_execution_gate import GreenExecutionGrant
from core.runner_vnext_leases import Lane, LeaseError
from core.runner_vnext_ledger import LedgerError, OperationIdentity, OperationLedger
from core.runner_vnext_scheduler import VNextScheduler


class ReconciliationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class OutcomeEvidence:
    operation_id: str
    idempotency_key: str
    adapter_id: str
    target_state_ref: str
    fence_token: int
    execution_grant_hash: str
    resources: tuple[str, ...]
    effects: tuple[str, ...]
    outcome: str
    reason_code: str
    before_state_ref: str
    after_state_ref: str
    validation_status: str
    evidence_refs: tuple[str, ...]
    fresh: bool
    rollback_requested: bool = False


class OutcomeProbe(Protocol):
    def probe(self, grant: GreenExecutionGrant) -> OutcomeEvidence: ...


class OutcomeEvidenceVerifier(Protocol):
    def verify(self, grant: GreenExecutionGrant, evidence: OutcomeEvidence) -> bool: ...


@dataclass(frozen=True)
class RollbackRequest:
    rollback_operation_id: str
    rollback_idempotency_key: str
    original_operation_id: str
    original_idempotency_key: str
    adapter_id: str
    fence_token: int
    resources: tuple[str, ...] = field(repr=False)
    original_effects: tuple[str, ...] = field(repr=False)
    target_before_state_ref: str = field(repr=False)
    current_after_state_ref: str = field(repr=False)
    reason_code: str = "ROLLBACK_REQUIRED"
    execution_authorized: bool = False


@dataclass(frozen=True)
class ReconciliationReceipt:
    status: str
    reason_code: str
    task_id: str
    node_id: str
    lane: str
    operation_id: str
    idempotency_key: str
    adapter_id: str
    fence_token: int
    privacy: str
    execution_grant_hash: str
    outcome: str
    validation_status: str | None
    touched_resource_refs: tuple[str, ...] = field(repr=False)
    before_state_ref: str | None = field(repr=False)
    after_state_ref: str | None = field(repr=False)
    rollback_required: bool = False
    rollback_request: RollbackRequest | None = field(default=None, repr=False)
    lease_released: bool = False
    needs_recovery: bool = True


class StartedOperationReconciler:
    """Reconciles unknown STARTED operations from typed evidence. It never re-runs effects."""

    def __init__(self, *, scheduler: VNextScheduler, ledger: OperationLedger) -> None:
        self._scheduler = scheduler
        self._ledger = ledger

    def reconcile(
        self,
        grant: GreenExecutionGrant,
        probe: OutcomeProbe,
        *,
        evidence_verifier: OutcomeEvidenceVerifier | None = None,
    ) -> ReconciliationReceipt:
        start = self._require_started(grant)
        try:
            evidence = probe.probe(grant)
        except Exception:
            return self._pending(grant, reason_code="RECONCILIATION_PROBE_FAILED")
        _validate_evidence(grant, evidence, expected_grant_hash=start.execution_grant_hash)
        if evidence.outcome == "UNKNOWN":
            return self._pending(grant, reason_code="RECONCILIATION_OUTCOME_UNKNOWN")
        if evidence_verifier is None:
            raise ReconciliationError("RECONCILIATION_EVIDENCE_VERIFIER_REQUIRED")
        if not evidence.fresh:
            raise ReconciliationError("RECONCILIATION_FRESH_EVIDENCE_REQUIRED")
        if not evidence.evidence_refs or any(not _public_ref(ref) for ref in evidence.evidence_refs):
            raise ReconciliationError("RECONCILIATION_EVIDENCE_REF_INVALID")
        if not evidence_verifier.verify(grant, evidence):
            raise ReconciliationError("RECONCILIATION_EVIDENCE_VERIFICATION_FAILED")

        success = evidence.outcome == "SUCCEEDED"
        if success and evidence.validation_status != "PASS":
            raise ReconciliationError("RECONCILIATION_SUCCESS_REQUIRES_PASS")
        if not success and evidence.validation_status != "FAIL":
            raise ReconciliationError("RECONCILIATION_FAILURE_REQUIRES_FAIL")
        if evidence.outcome == "NO_EFFECT" and evidence.before_state_ref != evidence.after_state_ref:
            raise ReconciliationError("RECONCILIATION_NO_EFFECT_STATE_MISMATCH")

        reason = "RECONCILED_NO_EFFECT" if evidence.outcome == "NO_EFFECT" else evidence.reason_code
        identity = OperationIdentity(grant.operation_id, grant.idempotency_key, grant.target_state_ref)
        try:
            self._ledger.finish(
                identity,
                fence_token=grant.fence_token,
                success=success,
                reason_code=reason,
                touched_resource_refs=evidence.resources,
                before_state_ref=evidence.before_state_ref,
                after_state_ref=evidence.after_state_ref,
                validation_status=evidence.validation_status,
            )
        except LedgerError as exc:
            raise ReconciliationError(exc.reason_code) from exc

        state_changed = evidence.before_state_ref != evidence.after_state_ref
        rollback_required = (not success) and (evidence.rollback_requested or state_changed or evidence.outcome == "PARTIAL")
        rollback = _rollback_request(grant, evidence) if rollback_required else None
        lease_released = self._release(grant)
        return ReconciliationReceipt(
            status="RECONCILED_COMPLETED" if success else "RECONCILED_FAILED",
            reason_code=reason,
            task_id=grant.task_id, node_id=grant.node_id, lane=grant.lane,
            operation_id=grant.operation_id, idempotency_key=grant.idempotency_key, adapter_id=grant.adapter_id,
            fence_token=grant.fence_token, privacy=grant.planner_envelope.privacy.value,
            execution_grant_hash=start.execution_grant_hash, outcome=evidence.outcome,
            validation_status=evidence.validation_status, touched_resource_refs=evidence.resources,
            before_state_ref=evidence.before_state_ref, after_state_ref=evidence.after_state_ref,
            rollback_required=rollback_required, rollback_request=rollback, lease_released=lease_released,
            needs_recovery=rollback_required or not lease_released,
        )

    def _require_started(self, grant: GreenExecutionGrant):
        if self._ledger.status(grant.idempotency_key) != "STARTED":
            raise ReconciliationError("RECONCILIATION_STARTED_LEDGER_REQUIRED")
        try:
            start = self._ledger.started_event(grant.idempotency_key)
            reservation = self._ledger.reservation_event(grant.idempotency_key)
            current_fence = self._ledger.current_fence_token(grant.idempotency_key)
        except LedgerError as exc:
            raise ReconciliationError(exc.reason_code) from exc
        expected_identity = OperationIdentity(grant.operation_id, grant.idempotency_key, grant.target_state_ref)
        if start.identity != expected_identity or reservation.identity != expected_identity:
            raise ReconciliationError("RECONCILIATION_LEDGER_IDENTITY_MISMATCH")
        if start.fence_token != grant.fence_token or current_fence != grant.fence_token:
            raise ReconciliationError("RECONCILIATION_LEDGER_FENCE_MISMATCH")
        grant_hash = execution_grant_hash(grant)
        if start.execution_grant_hash != grant_hash:
            raise ReconciliationError("RECONCILIATION_GRANT_HASH_MISMATCH")
        if start.reservation_scope_hash != reservation.reservation_scope_hash:
            raise ReconciliationError("RECONCILIATION_SCOPE_HASH_MISMATCH")
        return start

    def _pending(self, grant: GreenExecutionGrant, *, reason_code: str) -> ReconciliationReceipt:
        start = self._require_started(grant)
        return ReconciliationReceipt(
            status="NEEDS_RECOVERY", reason_code=reason_code, task_id=grant.task_id, node_id=grant.node_id,
            lane=grant.lane, operation_id=grant.operation_id, idempotency_key=grant.idempotency_key,
            adapter_id=grant.adapter_id, fence_token=grant.fence_token, privacy=grant.planner_envelope.privacy.value,
            execution_grant_hash=start.execution_grant_hash, outcome="UNKNOWN", validation_status=None,
            touched_resource_refs=(), before_state_ref=None, after_state_ref=None, rollback_required=False,
            rollback_request=None, lease_released=False, needs_recovery=True,
        )

    def _release(self, grant: GreenExecutionGrant) -> bool:
        try:
            self._scheduler.release_exact(
                lane=Lane(grant.lane), scope_key=grant.lease_scope_key, owner=grant.node_id, fence_token=grant.fence_token
            )
            return True
        except (LeaseError, ValueError):
            return False

    @staticmethod
    def public_projection(receipt: ReconciliationReceipt) -> dict[str, object]:
        projection: dict[str, object] = {
            "schema": "skeleton.runner_operation_reconciliation_receipt.v1",
            "status": receipt.status, "reason_code": receipt.reason_code, "lane": receipt.lane,
            "node_id": receipt.node_id, "fence_token": receipt.fence_token, "privacy": receipt.privacy,
            "task_id_hash": _hash(receipt.task_id), "operation_id_hash": _hash(receipt.operation_id),
            "idempotency_key_hash": _hash(receipt.idempotency_key), "adapter_id": receipt.adapter_id,
            "execution_grant_hash": receipt.execution_grant_hash, "outcome": receipt.outcome,
            "validation_status": receipt.validation_status, "touched_resource_count": len(receipt.touched_resource_refs),
            "touched_resource_hash": _refs_hash(receipt.touched_resource_refs),
            "before_state_ref_hash": _hash(receipt.before_state_ref or "none"),
            "after_state_ref_hash": _hash(receipt.after_state_ref or "none"),
            "state_changed": receipt.before_state_ref is not None and receipt.before_state_ref != receipt.after_state_ref,
            "rollback_required": receipt.rollback_required, "lease_released": receipt.lease_released,
            "needs_recovery": receipt.needs_recovery,
            "rollback_request_id_hash": _hash(receipt.rollback_request.rollback_idempotency_key) if receipt.rollback_request else None,
        }
        if receipt.privacy == "PUBLIC_SAFE":
            projection.update({
                "task_id": receipt.task_id, "operation_id": receipt.operation_id,
                "idempotency_key": receipt.idempotency_key,
                "touched_resource_refs": list(receipt.touched_resource_refs),
                "before_state_ref": receipt.before_state_ref, "after_state_ref": receipt.after_state_ref,
                "rollback_request_id": receipt.rollback_request.rollback_idempotency_key if receipt.rollback_request else None,
            })
        return projection

    @staticmethod
    def rollback_public_projection(request: RollbackRequest, *, privacy: str) -> dict[str, object]:
        projection: dict[str, object] = {
            "schema": "skeleton.runner_rollback_request.v1",
            "privacy": privacy, "rollback_operation_id_hash": _hash(request.rollback_operation_id),
            "rollback_idempotency_key_hash": _hash(request.rollback_idempotency_key),
            "original_operation_id_hash": _hash(request.original_operation_id),
            "original_idempotency_key_hash": _hash(request.original_idempotency_key),
            "adapter_id": request.adapter_id, "fence_token": request.fence_token,
            "resource_count": len(request.resources), "resource_hash": _refs_hash(request.resources),
            "original_effect_count": len(request.original_effects), "original_effect_hash": _refs_hash(request.original_effects),
            "target_before_state_ref_hash": _hash(request.target_before_state_ref),
            "current_after_state_ref_hash": _hash(request.current_after_state_ref),
            "reason_code": request.reason_code, "execution_authorized": request.execution_authorized,
        }
        if privacy == "PUBLIC_SAFE":
            projection.update({
                "rollback_operation_id": request.rollback_operation_id,
                "rollback_idempotency_key": request.rollback_idempotency_key,
                "original_operation_id": request.original_operation_id,
                "original_idempotency_key": request.original_idempotency_key,
                "resources": list(request.resources), "original_effects": list(request.original_effects),
                "target_before_state_ref": request.target_before_state_ref,
                "current_after_state_ref": request.current_after_state_ref,
            })
        return projection


def _validate_evidence(grant: GreenExecutionGrant, evidence: OutcomeEvidence, *, expected_grant_hash: str) -> None:
    if evidence.outcome not in {"UNKNOWN", "NO_EFFECT", "SUCCEEDED", "FAILED", "PARTIAL"}:
        raise ReconciliationError("RECONCILIATION_OUTCOME_INVALID")
    if evidence.validation_status not in {"PASS", "FAIL", "UNKNOWN"}:
        raise ReconciliationError("RECONCILIATION_VALIDATION_INVALID")
    if not evidence.reason_code or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in evidence.reason_code):
        raise ReconciliationError("RECONCILIATION_REASON_INVALID")
    expected = (grant.operation_id, grant.idempotency_key, grant.adapter_id, grant.target_state_ref, grant.fence_token, expected_grant_hash, grant.planner_envelope.resources, grant.planner_envelope.effects)
    actual = (evidence.operation_id, evidence.idempotency_key, evidence.adapter_id, evidence.target_state_ref, evidence.fence_token, evidence.execution_grant_hash, evidence.resources, evidence.effects)
    if actual != expected:
        raise ReconciliationError("RECONCILIATION_EVIDENCE_BINDING_MISMATCH")
    if evidence.outcome == "UNKNOWN":
        return
    for ref in (*evidence.resources, evidence.before_state_ref, evidence.after_state_ref):
        if not _public_ref(ref):
            raise ReconciliationError("RECONCILIATION_PUBLIC_REF_REQUIRED")


def _rollback_request(grant: GreenExecutionGrant, evidence: OutcomeEvidence) -> RollbackRequest:
    seed = json.dumps({
        "operation_id": grant.operation_id, "idempotency_key": grant.idempotency_key,
        "execution_grant_hash": evidence.execution_grant_hash, "after_state_ref": evidence.after_state_ref,
    }, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return RollbackRequest(
        rollback_operation_id=f"rollback:{digest}", rollback_idempotency_key=f"rollback:{digest}",
        original_operation_id=grant.operation_id, original_idempotency_key=grant.idempotency_key,
        adapter_id=grant.adapter_id, fence_token=grant.fence_token, resources=evidence.resources, original_effects=evidence.effects,
        target_before_state_ref=evidence.before_state_ref, current_after_state_ref=evidence.after_state_ref,
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _refs_hash(values: tuple[str, ...]) -> str:
    return _hash(json.dumps(sorted(values), separators=(",", ":")))


def _public_ref(value: str) -> bool:
    return bool(value) and not value.startswith(("/", "~")) and "\\" not in value and ".." not in value and ":" in value and not any(ch.isspace() for ch in value)
