from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_adapters import AdapterManifest, AdapterPlanner
from core.runner_vnext_contracts import EffectClass, OperationIR, PrivacyClass, Reversibility, UniversalTask
from core.runner_vnext_control_plane import GreenControlPlane
from core.runner_vnext_environment import LaneEnvironmentPolicy, LaneEnvironmentPolicyRegistry
from core.runner_vnext_execution import GreenExecutionError, GreenExecutionLifecycle, GreenExecutionResult
from core.runner_vnext_execution_gate import GreenExecutionGate
from core.runner_vnext_leases import Lane, LaneLeaseStore
from core.runner_vnext_ledger import OperationIdentity, OperationLedger
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot, RoutePlanner
from core.runner_vnext_scheduler import LaneRequest, VNextScheduler

ROOT = Path(__file__).resolve().parents[1]
NOW = 100.0


def task() -> UniversalTask:
    return UniversalTask(
        task_id="task:1", intent="write", domain="github", target_resources=("repo:core/a.py",),
        required_capabilities=("repository_write_allowlisted",), privacy=PrivacyClass.PUBLIC_SAFE,
        reversibility=Reversibility.REVERSIBLE, expected_effects=("workspace_write",),
        validation=("pytest",), rollback=("git:reset",), idempotency_key="idem:1",
    )


def operation() -> OperationIR:
    return OperationIR("op:1", "workspace_write", ("repo:core/a.py",), ("workspace_write",), "idem:1")


class TargetVerifier:
    def current_ref(self, handoff) -> str:
        return "git:abc"


def build_runtime():
    nodes = NodeCapabilityRegistry()
    nodes.register(NodeCapabilitySnapshot(
        node_id="node:runner-1", generation=3, route_rank=1,
        capabilities=("repository_write_allowlisted",), supported_adapters=("adapter:repo",),
        supported_lanes=(Lane.CODEGEN,), privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
        resource_patterns=("repo:*",), observed_at=90.0, expires_at=200.0,
        attestation_ref="attestation:runner-1-3",
    ), now=NOW)
    envs = LaneEnvironmentPolicyRegistry({
        "lane:codegen": LaneEnvironmentPolicy(
            "lane:codegen", ("HOME", "PATH"), (),
            ("OPENROUTER_API_KEY", "BWS_ACCESS_TOKEN", "SKELETON_TG_BOT"), (),
        )
    })
    store = LaneLeaseStore(clock=lambda: NOW)
    scheduler = VNextScheduler(store)
    ledger = OperationLedger()
    manifest = AdapterManifest(
        adapter_id="adapter:repo", operation_kinds=("workspace_write",),
        capabilities=("repository_write_allowlisted",), allowed_effect_classes=(EffectClass.GREEN,),
        resource_patterns=("repo:*",), privacy_classes=(PrivacyClass.PUBLIC_SAFE,), privileged_pep_required=False,
    )
    control = GreenControlPlane(
        route_planner=RoutePlanner(nodes), environment_registry=envs, scheduler=scheduler,
        adapter_planner=AdapterPlanner({"adapter:repo": manifest}), ledger=ledger,
    )
    handoff = control.prepare(
        task=task(), operation=operation(), adapter_id="adapter:repo", lane=Lane.CODEGEN,
        lease_scope_key="repo:branch-main", target_state_ref="git:abc", ttl_seconds=30, now=NOW,
        parent_environment={"HOME":"/home/agent", "PATH":"/usr/bin", "OPENROUTER_API_KEY":"parent-secret"},
        injected_environment={},
    )
    gate = GreenExecutionGate(
        nodes=nodes, environment_registry=envs, scheduler=scheduler, ledger=ledger,
        target_state_verifier=TargetVerifier(),
    )
    grant = gate.grant(handoff, now=NOW)
    return scheduler, ledger, grant


def result(grant, *, outcome="SUCCEEDED", before="git:abc", after="git:def", validation=None, rollback=False, **overrides):
    values = dict(
        operation_id=grant.operation_id, idempotency_key=grant.idempotency_key, adapter_id=grant.adapter_id,
        target_state_ref=grant.target_state_ref, fence_token=grant.fence_token,
        resources=grant.planner_envelope.resources, effects=grant.planner_envelope.effects, outcome=outcome,
        reason_code="EXECUTION_PASS" if outcome == "SUCCEEDED" else "EXECUTION_FAILED",
        before_state_ref=before, after_state_ref=after,
        validation_status=validation or ("PASS" if outcome == "SUCCEEDED" else "FAIL"),
        rollback_requested=rollback,
    )
    values.update(overrides)
    return GreenExecutionResult(**values)


class FakeExecutor:
    def __init__(self, produced=None, *, raises=False) -> None:
        self.produced = produced
        self.raises = raises
        self.calls = 0
    def execute(self, grant):
        self.calls += 1
        if self.raises:
            raise RuntimeError("private adapter error details")
        return self.produced or result(grant)


def test_success_terminalizes_once_and_releases_exact_lease() -> None:
    scheduler, ledger, grant = build_runtime()
    executor = FakeExecutor(result(grant))
    lifecycle = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger)
    receipt = lifecycle.run(grant, executor)
    assert executor.calls == 1
    assert receipt.terminal_status == "COMPLETED" and receipt.lease_released is True
    assert receipt.rollback_required is False and receipt.needs_recovery is False
    assert ledger.status(grant.idempotency_key) == "COMPLETED"
    assert scheduler.current(lane=Lane.CODEGEN, scope_key=grant.lease_scope_key) is None
    assert [e.event_type for e in ledger.history(grant.idempotency_key)] == ["RESERVED", "COMPLETED"]


def test_effect_start_is_durable_and_retry_never_invokes_adapter_again(tmp_path) -> None:
    scheduler, ledger, grant = build_runtime()
    lifecycle = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger)
    lifecycle.begin(grant)
    assert ledger.status(grant.idempotency_key) == "STARTED"
    blocked = FakeExecutor(result(grant))
    with pytest.raises(GreenExecutionError, match="EXECUTION_ALREADY_STARTED_NEEDS_RECOVERY"):
        lifecycle.run(grant, blocked)
    assert blocked.calls == 0


def test_adapter_exception_leaves_started_state_for_recovery_and_hides_raw_error() -> None:
    scheduler, ledger, grant = build_runtime()
    lifecycle = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger)
    executor = FakeExecutor(raises=True)
    with pytest.raises(GreenExecutionError, match="EXECUTION_ADAPTER_EXCEPTION_NEEDS_RECOVERY") as exc:
        lifecycle.run(grant, executor)
    assert "private adapter error details" not in str(exc.value)
    assert ledger.status(grant.idempotency_key) == "STARTED"
    assert scheduler.current(lane=Lane.CODEGEN, scope_key=grant.lease_scope_key) is not None


def test_forged_result_after_effect_never_records_false_success() -> None:
    scheduler, ledger, grant = build_runtime()
    forged = result(grant, adapter_id="adapter:other")
    lifecycle = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger)
    with pytest.raises(GreenExecutionError, match="EXECUTION_RESULT_BINDING_MISMATCH_NEEDS_RECOVERY"):
        lifecycle.run(grant, FakeExecutor(forged))
    assert ledger.status(grant.idempotency_key) == "STARTED"
    assert scheduler.current(lane=Lane.CODEGEN, scope_key=grant.lease_scope_key) is not None


def test_partial_changed_state_terminalizes_failed_and_requests_rollback() -> None:
    scheduler, ledger, grant = build_runtime()
    partial = result(grant, outcome="PARTIAL", before="git:abc", after="git:partial")
    receipt = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(grant, FakeExecutor(partial))
    assert receipt.terminal_status == "FAILED"
    assert receipt.rollback_required is True and receipt.needs_recovery is True
    assert receipt.lease_released is True
    assert ledger.status(grant.idempotency_key) == "FAILED"


def test_failed_without_state_change_can_finish_without_rollback() -> None:
    scheduler, ledger, grant = build_runtime()
    failed = result(grant, outcome="FAILED", before="git:abc", after="git:abc")
    receipt = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(grant, FakeExecutor(failed))
    assert receipt.terminal_status == "FAILED" and receipt.rollback_required is False
    assert receipt.needs_recovery is False and receipt.lease_released is True


def test_stale_lease_blocks_before_adapter_effect() -> None:
    scheduler, ledger, grant = build_runtime()
    request = LaneRequest(grant.task_id, Lane.CODEGEN, grant.node_id, grant.lease_scope_key, 30, grant.target_state_ref)
    scheduler.release(request, fence_token=grant.fence_token)
    executor = FakeExecutor(result(grant))
    with pytest.raises(GreenExecutionError, match="EXECUTION_LEASE_MISSING_OR_EXPIRED"):
        GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(grant, executor)
    assert executor.calls == 0
    assert ledger.status(grant.idempotency_key) == "RESERVED"


def test_grant_and_result_contracts_are_closed_and_public_receipt_value_free() -> None:
    scheduler, ledger, grant = build_runtime()
    lifecycle = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger)
    receipt = lifecycle.run(grant, FakeExecutor(result(grant)))
    public = lifecycle.public_projection(receipt)
    blob = json.dumps(public)
    assert "parent-secret" not in blob
    assert "environment" not in public and "planner_envelope" not in public and "lease_scope_key" not in public
    assert not any(k in public for k in ("shell", "argv", "command", "exception"))
    schema = json.loads((ROOT / "schemas" / "runner_green_execution_receipt.schema.json").read_text())
    jsonschema.validate(public, schema)
    result_schema = json.loads((ROOT / "schemas" / "runner_green_execution_result.schema.json").read_text())
    payload = {
        "schema":"skeleton.runner_green_execution_result.v1",
        "operation_id":grant.operation_id, "idempotency_key":grant.idempotency_key, "adapter_id":grant.adapter_id,
        "target_state_ref":grant.target_state_ref, "fence_token":grant.fence_token,
        "resources":list(grant.planner_envelope.resources), "effects":list(grant.planner_envelope.effects),
        "outcome":"SUCCEEDED", "reason_code":"EXECUTION_PASS", "before_state_ref":"git:abc",
        "after_state_ref":"git:def", "validation_status":"PASS", "rollback_requested":False,
    }
    jsonschema.validate(payload, result_schema)


def test_terminal_or_started_ledger_blocks_duplicate_run_before_adapter() -> None:
    scheduler, ledger, grant = build_runtime()
    lifecycle = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger)
    lifecycle.run(grant, FakeExecutor(result(grant)))
    second = FakeExecutor(result(grant))
    with pytest.raises(GreenExecutionError, match="EXECUTION_LEDGER_STATE_MISMATCH"):
        lifecycle.run(grant, second)
    assert second.calls == 0


def test_private_receipt_projection_hashes_private_refs_instead_of_exposing_them() -> None:
    scheduler, ledger, grant = build_runtime()
    lifecycle = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger)
    receipt = lifecycle.run(grant, FakeExecutor(result(grant)))
    private = replace(
        receipt, privacy="PRIVATE", task_id="task:customer-sensitive",
        operation_id="op:customer-sensitive", idempotency_key="idem:customer-sensitive",
        lease_scope_key="private:customer/path",
        touched_resource_refs=("private:customer/path/file",),
        before_state_ref="private:customer/before", after_state_ref="private:customer/after",
    )
    public = lifecycle.public_projection(private)
    blob = json.dumps(public)
    assert "customer" not in blob and "private:" not in blob
    assert "task_id" not in public and "operation_id" not in public and "idempotency_key" not in public
    assert "touched_resource_refs" not in public
    assert "before_state_ref" not in public and "after_state_ref" not in public
    assert public["privacy"] == "PRIVATE" and public["touched_resource_count"] == 1
    schema = json.loads((ROOT / "schemas" / "runner_green_execution_receipt.schema.json").read_text())
    jsonschema.validate(public, schema)
