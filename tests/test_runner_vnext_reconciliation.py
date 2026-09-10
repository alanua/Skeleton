from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_adapters import ExecutionEnvelope
from core.runner_vnext_contracts import EffectClass, PrivacyClass
from core.runner_vnext_execution import execution_grant_hash
from core.runner_vnext_execution_gate import GreenExecutionGrant, planner_envelope_hash
from core.runner_vnext_leases import Lane, LaneLeaseStore
from core.runner_vnext_ledger import OperationIdentity, OperationLedger
from core.runner_vnext_pep import reservation_scope_fingerprint
from core.runner_vnext_reconciliation import OutcomeEvidence, ReconciliationError, StartedOperationReconciler
from core.runner_vnext_scheduler import LaneRequest, VNextScheduler

ROOT = Path(__file__).resolve().parents[1]


def runtime(*, privacy=PrivacyClass.PUBLIC_SAFE, sensitive=False):
    task_id = "task:customer-sensitive" if sensitive else "task:1"
    op_id = "op:customer-sensitive" if sensitive else "op:1"
    idem = "idem:customer-sensitive" if sensitive else "idem:1"
    target = "private:customer/state" if sensitive else "git:abc"
    resource = "private:customer/path/file" if sensitive else "repo:core/a.py"
    envelope = ExecutionEnvelope(
        operation_id=op_id, adapter_id="adapter:repo", target_state_ref=target, fence_token=1,
        idempotency_key=idem, resources=(resource,), effects=("workspace_write",),
        effect_class=EffectClass.GREEN, policy_reason_code="GREEN_AUTONOMOUS_TYPED_EFFECT",
        required_capabilities=("repository_write_allowlisted",), privacy=privacy,
    )
    scope_hash = reservation_scope_fingerprint(envelope)
    grant = GreenExecutionGrant(
        task_id=task_id, node_id="node:runner-1", node_generation=1, node_snapshot_hash="a"*64,
        node_attestation_ref="attestation:runner-1", lane="codegen", lease_scope_key="private:customer/scope" if sensitive else "repo:branch-main",
        fence_token=1, environment_policy_hash="b"*64, operation_id=op_id, idempotency_key=idem,
        adapter_id="adapter:repo", target_state_ref=target, reservation_scope_hash=scope_hash,
        planner_envelope_hash=planner_envelope_hash(envelope), policy_reason_code="GREEN_AUTONOMOUS_TYPED_EFFECT",
        authorization_status="AUTHORIZED_GREEN_BOUNDED", environment={}, planner_envelope=envelope,
    )
    store = LaneLeaseStore(clock=lambda: 100.0)
    scheduler = VNextScheduler(store)
    scheduler.reserve(LaneRequest(task_id, Lane.CODEGEN, "node:runner-1", grant.lease_scope_key, 30, target))
    ledger = OperationLedger()
    identity = OperationIdentity(op_id, idem, target)
    ledger.reserve(identity, fence_token=1, reservation_scope_hash=scope_hash)
    ledger.mark_started(identity, fence_token=1, execution_grant_hash=execution_grant_hash(grant))
    return scheduler, ledger, grant


def evidence(grant, *, outcome="SUCCEEDED", before=None, after=None, validation=None, rollback=False, **overrides):
    before = before or grant.target_state_ref
    after = after or ("git:def" if grant.planner_envelope.privacy is PrivacyClass.PUBLIC_SAFE else "private:customer/after")
    values = dict(
        operation_id=grant.operation_id, idempotency_key=grant.idempotency_key, adapter_id=grant.adapter_id,
        target_state_ref=grant.target_state_ref, fence_token=grant.fence_token, execution_grant_hash=execution_grant_hash(grant),
        resources=grant.planner_envelope.resources, effects=grant.planner_envelope.effects, outcome=outcome,
        reason_code="RECONCILIATION_PASS" if outcome == "SUCCEEDED" else "RECONCILIATION_FAILED",
        before_state_ref=before, after_state_ref=after,
        validation_status=validation or ("PASS" if outcome == "SUCCEEDED" else ("UNKNOWN" if outcome == "UNKNOWN" else "FAIL")),
        evidence_refs=("attestation:probe-1",), fresh=True, rollback_requested=rollback,
    )
    values.update(overrides)
    return OutcomeEvidence(**values)


class Verifier:
    def __init__(self, result=True) -> None:
        self.result = result
    def verify(self, grant, evidence) -> bool:
        return self.result


class Probe:
    def __init__(self, value=None, *, raises=False) -> None:
        self.value = value
        self.raises = raises
        self.calls = 0
    def probe(self, grant):
        self.calls += 1
        if self.raises:
            raise RuntimeError("private probe failure detail")
        return self.value or evidence(grant)


def test_successful_probe_terminalizes_started_operation_and_releases_lease() -> None:
    scheduler, ledger, grant = runtime()
    receipt = StartedOperationReconciler(scheduler=scheduler, ledger=ledger).reconcile(grant, Probe(evidence(grant)), evidence_verifier=Verifier())
    assert receipt.status == "RECONCILED_COMPLETED" and receipt.needs_recovery is False
    assert receipt.lease_released is True and receipt.rollback_request is None
    assert ledger.status(grant.idempotency_key) == "COMPLETED"
    assert scheduler.current(lane=Lane.CODEGEN, scope_key=grant.lease_scope_key) is None


def test_unknown_outcome_never_retries_or_terminalizes() -> None:
    scheduler, ledger, grant = runtime()
    unknown = evidence(grant, outcome="UNKNOWN", before="state:unknown", after="state:unknown")
    receipt = StartedOperationReconciler(scheduler=scheduler, ledger=ledger).reconcile(grant, Probe(unknown))
    assert receipt.status == "NEEDS_RECOVERY" and receipt.outcome == "UNKNOWN"
    assert receipt.lease_released is False and receipt.needs_recovery is True
    assert ledger.status(grant.idempotency_key) == "STARTED"


def test_probe_exception_is_value_free_and_leaves_started_truth() -> None:
    scheduler, ledger, grant = runtime()
    receipt = StartedOperationReconciler(scheduler=scheduler, ledger=ledger).reconcile(grant, Probe(raises=True))
    assert receipt.reason_code == "RECONCILIATION_PROBE_FAILED"
    assert "private probe failure detail" not in repr(receipt)
    assert ledger.status(grant.idempotency_key) == "STARTED"


def test_forged_probe_binding_fails_closed_without_terminal_mutation() -> None:
    scheduler, ledger, grant = runtime()
    forged = evidence(grant, adapter_id="adapter:other")
    with pytest.raises(ReconciliationError, match="RECONCILIATION_EVIDENCE_BINDING_MISMATCH"):
        StartedOperationReconciler(scheduler=scheduler, ledger=ledger).reconcile(grant, Probe(forged), evidence_verifier=Verifier())
    assert ledger.status(grant.idempotency_key) == "STARTED"


def test_partial_changed_state_emits_non_executable_rollback_request() -> None:
    scheduler, ledger, grant = runtime()
    partial = evidence(grant, outcome="PARTIAL", before="git:abc", after="git:partial")
    receipt = StartedOperationReconciler(scheduler=scheduler, ledger=ledger).reconcile(grant, Probe(partial), evidence_verifier=Verifier())
    assert receipt.status == "RECONCILED_FAILED" and receipt.rollback_required is True
    assert receipt.rollback_request is not None and receipt.rollback_request.execution_authorized is False
    assert receipt.rollback_request.rollback_idempotency_key != grant.idempotency_key
    assert receipt.rollback_request.resources == grant.planner_envelope.resources
    assert receipt.rollback_request.original_effects == grant.planner_envelope.effects
    assert ledger.status(grant.idempotency_key) == "FAILED"
    rollback_public = StartedOperationReconciler.rollback_public_projection(receipt.rollback_request, privacy="PUBLIC_SAFE")
    schema = json.loads((ROOT / "schemas" / "runner_rollback_request.schema.json").read_text())
    jsonschema.validate(rollback_public, schema)


def test_no_effect_requires_same_state_and_terminalizes_failed_without_rollback() -> None:
    scheduler, ledger, grant = runtime()
    no_effect = evidence(grant, outcome="NO_EFFECT", before="git:abc", after="git:abc")
    receipt = StartedOperationReconciler(scheduler=scheduler, ledger=ledger).reconcile(grant, Probe(no_effect), evidence_verifier=Verifier())
    assert receipt.reason_code == "RECONCILED_NO_EFFECT" and receipt.rollback_required is False
    assert receipt.status == "RECONCILED_FAILED" and ledger.status(grant.idempotency_key) == "FAILED"
    scheduler2, ledger2, grant2 = runtime()
    bad = evidence(grant2, outcome="NO_EFFECT", before="git:abc", after="git:changed")
    with pytest.raises(ReconciliationError, match="RECONCILIATION_NO_EFFECT_STATE_MISMATCH"):
        StartedOperationReconciler(scheduler=scheduler2, ledger=ledger2).reconcile(grant2, Probe(bad), evidence_verifier=Verifier())
    assert ledger2.status(grant2.idempotency_key) == "STARTED"


def test_success_requires_pass_and_failure_requires_fail() -> None:
    scheduler, ledger, grant = runtime()
    with pytest.raises(ReconciliationError, match="RECONCILIATION_SUCCESS_REQUIRES_PASS"):
        StartedOperationReconciler(scheduler=scheduler, ledger=ledger).reconcile(grant, Probe(evidence(grant, validation="FAIL")), evidence_verifier=Verifier())
    assert ledger.status(grant.idempotency_key) == "STARTED"
    scheduler2, ledger2, grant2 = runtime()
    bad_failed = evidence(grant2, outcome="FAILED", before="git:abc", after="git:abc", validation="PASS")
    with pytest.raises(ReconciliationError, match="RECONCILIATION_FAILURE_REQUIRES_FAIL"):
        StartedOperationReconciler(scheduler=scheduler2, ledger=ledger2).reconcile(grant2, Probe(bad_failed), evidence_verifier=Verifier())



def test_terminal_reconciliation_requires_fresh_external_evidence_verification() -> None:
    scheduler, ledger, grant = runtime()
    reconciler = StartedOperationReconciler(scheduler=scheduler, ledger=ledger)
    with pytest.raises(ReconciliationError, match="RECONCILIATION_EVIDENCE_VERIFIER_REQUIRED"):
        reconciler.reconcile(grant, Probe(evidence(grant)))
    assert ledger.status(grant.idempotency_key) == "STARTED"

    scheduler2, ledger2, grant2 = runtime()
    stale = evidence(grant2, fresh=False)
    with pytest.raises(ReconciliationError, match="RECONCILIATION_FRESH_EVIDENCE_REQUIRED"):
        StartedOperationReconciler(scheduler=scheduler2, ledger=ledger2).reconcile(grant2, Probe(stale), evidence_verifier=Verifier())
    assert ledger2.status(grant2.idempotency_key) == "STARTED"

    scheduler3, ledger3, grant3 = runtime()
    with pytest.raises(ReconciliationError, match="RECONCILIATION_EVIDENCE_VERIFICATION_FAILED"):
        StartedOperationReconciler(scheduler=scheduler3, ledger=ledger3).reconcile(grant3, Probe(evidence(grant3)), evidence_verifier=Verifier(False))
    assert ledger3.status(grant3.idempotency_key) == "STARTED"

def test_private_projection_hashes_all_private_identity_and_state_refs() -> None:
    scheduler, ledger, grant = runtime(privacy=PrivacyClass.PRIVATE, sensitive=True)
    partial = evidence(grant, outcome="PARTIAL", before="private:customer/before", after="private:customer/after")
    reconciler = StartedOperationReconciler(scheduler=scheduler, ledger=ledger)
    receipt = reconciler.reconcile(grant, Probe(partial), evidence_verifier=Verifier())
    public = reconciler.public_projection(receipt)
    blob = json.dumps(public)
    assert "customer" not in blob and "private:" not in blob
    assert "task_id" not in public and "operation_id" not in public and "idempotency_key" not in public
    assert "touched_resource_refs" not in public and "before_state_ref" not in public and "after_state_ref" not in public
    assert public["privacy"] == "PRIVATE"
    schema = json.loads((ROOT / "schemas" / "runner_operation_reconciliation_receipt.schema.json").read_text())
    jsonschema.validate(public, schema)
    assert receipt.rollback_request is not None
    rollback_public = reconciler.rollback_public_projection(receipt.rollback_request, privacy="PRIVATE")
    rollback_blob = json.dumps(rollback_public)
    assert "customer" not in rollback_blob and "private:" not in rollback_blob
    assert "resources" not in rollback_public and "target_before_state_ref" not in rollback_public
    rollback_schema = json.loads((ROOT / "schemas" / "runner_rollback_request.schema.json").read_text())
    jsonschema.validate(rollback_public, rollback_schema)


def test_reconciliation_receipt_schema_validates_public_safe_projection() -> None:
    scheduler, ledger, grant = runtime()
    reconciler = StartedOperationReconciler(scheduler=scheduler, ledger=ledger)
    receipt = reconciler.reconcile(grant, Probe(evidence(grant)), evidence_verifier=Verifier())
    public = reconciler.public_projection(receipt)
    schema = json.loads((ROOT / "schemas" / "runner_operation_reconciliation_receipt.schema.json").read_text())
    jsonschema.validate(public, schema)
    assert not any(k in public for k in ("shell", "argv", "command", "exception"))
