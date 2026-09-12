from __future__ import annotations

from dataclasses import replace

import pytest

from core.runner_task import RunnerTask
from core.runner_vnext_adapters import AdapterManifest, AdapterPlanner
from core.runner_vnext_contracts import EffectClass, PrivacyClass
from core.runner_vnext_control_plane import GreenControlPlane
from core.runner_vnext_environment import LaneEnvironmentPolicy, LaneEnvironmentPolicyRegistry
from core.runner_vnext_execution import GreenExecutionError, GreenExecutionLifecycle
from core.runner_vnext_execution_gate import GreenExecutionGate
from core.runner_vnext_leases import Lane, LaneLeaseStore
from core.runner_vnext_ledger import OperationLedger
from core.runner_vnext_live_codegen import (
    LiveCodegenAdapter,
    LiveCodegenBackendResult,
    LiveCodegenBindingError,
    compile_live_codegen_plan,
)
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot, RoutePlanner
from core.runner_vnext_scheduler import VNextScheduler

NOW = 100.0
BASE = "1be214a24fc089efc80e8c794e380c697ae0994c"


def runner_task(**overrides) -> RunnerTask:
    values = {
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "branch": "runner/vnext-source-adapter-capability-binding-post-4088-v4",
        "base_sha": BASE,
        "task_kind": "code_edit",
        "payload": {"operation": "vnext_source_adapter_capability_binding_post_4088_v4"},
        "requested_capabilities": ["repository_read", "repository_write_allowlisted", "test_execution"],
        "allowed_files": ["app/codegen_target.py", "tests/codegen_target_test.py"],
        "forbidden_actions": ["scripts/runner_poll_github_tasks.py edit"],
        "validation_commands": [["python3", "-m", "pytest", "-q", "tests/test_runner_vnext_live_codegen.py"]],
        "validation_timeout_seconds": 1800,
        "expected_output": ["source-ref identity divergence PASS"],
        "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
        "approval_reference": "chat:20260912-autonomous-runner-vnext-completion",
        "idempotency_key": "vnext-source-adapter-cap-binding-post-4088-1be214a-v4",
    }
    values.update(overrides)
    return RunnerTask.from_mapping(values)


def build_runtime(task: RunnerTask | None = None, *, source="github:issue-4092", adapter_id="adapter:live-codegen"):
    task = task or runner_task()
    plan = compile_live_codegen_plan(task, source_task_ref=source, adapter_id=adapter_id)
    nodes = NodeCapabilityRegistry()
    nodes.register(
        NodeCapabilitySnapshot(
            node_id="node:runner-1",
            generation=3,
            route_rank=1,
            capabilities=plan.task.required_capabilities,
            supported_adapters=(adapter_id,),
            supported_lanes=(Lane.CODEGEN,),
            privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
            resource_patterns=("repo:*",),
            observed_at=90.0,
            expires_at=200.0,
            attestation_ref="attestation:runner-1-3",
        ),
        now=NOW,
    )
    envs = LaneEnvironmentPolicyRegistry(
        {"lane:codegen": LaneEnvironmentPolicy("lane:codegen", ("HOME", "PATH"), (), (), ())}
    )
    scheduler = VNextScheduler(LaneLeaseStore(clock=lambda: NOW))
    ledger = OperationLedger()
    manifest = AdapterManifest(
        adapter_id=adapter_id,
        operation_kinds=("workspace_write",),
        capabilities=plan.task.required_capabilities,
        allowed_effect_classes=(EffectClass.GREEN,),
        resource_patterns=("repo:*",),
        privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
        privileged_pep_required=False,
    )
    control = GreenControlPlane(
        route_planner=RoutePlanner(nodes),
        environment_registry=envs,
        scheduler=scheduler,
        adapter_planner=AdapterPlanner({adapter_id: manifest}),
        ledger=ledger,
    )
    handoff = control.prepare(
        task=plan.task,
        operation=plan.operation,
        adapter_id=adapter_id,
        lane=Lane.CODEGEN,
        lease_scope_key="repo:branch-main",
        target_state_ref=plan.binding.target_state_ref,
        ttl_seconds=30,
        now=NOW,
        parent_environment={"HOME": "/home/agent", "PATH": "/usr/bin"},
        injected_environment={},
    )
    gate = GreenExecutionGate(
        nodes=nodes,
        environment_registry=envs,
        scheduler=scheduler,
        ledger=ledger,
        target_state_verifier=type("Verifier", (), {"current_ref": lambda self, handoff: plan.binding.target_state_ref})(),
    )
    return plan, scheduler, ledger, gate.grant(handoff, now=NOW)


class Backend:
    def __init__(self, result: LiveCodegenBackendResult | None = None) -> None:
        self.result = result
        self.calls = 0
        self.requests = []

    def execute(self, request):
        self.calls += 1
        self.requests.append(request)
        return self.result or LiveCodegenBackendResult(
            touched_resource_refs=(request.resources[0],),
            before_state_ref=request.target_state_ref,
            after_state_ref="git:def",
            validation_status="PASS",
        )


def test_composite_binding_changes_for_source_adapter_and_task_material() -> None:
    task = runner_task()
    base = compile_live_codegen_plan(task, source_task_ref="github:issue-4092", adapter_id="adapter:live-codegen")
    other_source = compile_live_codegen_plan(task, source_task_ref="github:issue-4090", adapter_id="adapter:live-codegen")
    other_adapter = compile_live_codegen_plan(task, source_task_ref="github:issue-4092", adapter_id="adapter:other")
    changed_payload = compile_live_codegen_plan(
        runner_task(payload={"operation": "changed"}),
        source_task_ref="github:issue-4092",
        adapter_id="adapter:live-codegen",
    )
    changed_allowed = compile_live_codegen_plan(
        runner_task(allowed_files=["app/other_codegen_target.py"]),
        source_task_ref="github:issue-4092",
        adapter_id="adapter:live-codegen",
    )
    changed_validation = compile_live_codegen_plan(
        runner_task(validation_commands=[["python3", "-m", "py_compile", "core/runner_vnext_live_codegen.py"]]),
        source_task_ref="github:issue-4092",
        adapter_id="adapter:live-codegen",
    )
    changed_idem = compile_live_codegen_plan(
        runner_task(idempotency_key="vnext-source-adapter-cap-binding-post-4088-1be214a-v5"),
        source_task_ref="github:issue-4092",
        adapter_id="adapter:live-codegen",
    )
    changed_base = compile_live_codegen_plan(
        runner_task(base_sha="2be214a24fc089efc80e8c794e380c697ae0994c"),
        source_task_ref="github:issue-4092",
        adapter_id="adapter:live-codegen",
    )
    changed_branch = compile_live_codegen_plan(
        runner_task(branch="runner/changed"),
        source_task_ref="github:issue-4092",
        adapter_id="adapter:live-codegen",
    )
    changed_caps = compile_live_codegen_plan(
        runner_task(requested_capabilities=["repository_read", "repository_write_allowlisted"]),
        source_task_ref="github:issue-4092",
        adapter_id="adapter:live-codegen",
    )
    identities = {
        base.binding.binding_hash,
        other_source.binding.binding_hash,
        other_adapter.binding.binding_hash,
        changed_payload.binding.binding_hash,
        changed_allowed.binding.binding_hash,
        changed_validation.binding.binding_hash,
        changed_idem.binding.binding_hash,
        changed_base.binding.binding_hash,
        changed_branch.binding.binding_hash,
        changed_caps.binding.binding_hash,
    }
    assert len(identities) == 10
    assert base.binding.task_id != other_source.binding.task_id
    assert base.binding.operation_id != other_adapter.binding.operation_id
    assert base.binding.idempotency_key != other_adapter.binding.idempotency_key
    assert base.binding.runner_task_hash in base.task.operator_boundary_evidence[0]


def test_live_codegen_executes_only_after_exact_grant_scope_match() -> None:
    plan, scheduler, ledger, grant = build_runtime()
    backend = Backend()
    receipt = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
        grant,
        LiveCodegenAdapter(plan=plan, backend=backend),
    )
    assert backend.calls == 1
    assert backend.requests[0].required_capabilities == plan.task.required_capabilities
    assert receipt.touched_resource_refs == (plan.operation.resources[0],)


def test_mutated_grant_capability_scope_fails_before_backend_invocation() -> None:
    plan, scheduler, ledger, grant = build_runtime()
    mutated_envelope = replace(grant.planner_envelope, required_capabilities=("repository_write_allowlisted",))
    mutated_grant = replace(grant, planner_envelope=mutated_envelope)
    backend = Backend()
    with pytest.raises(GreenExecutionError, match="EXECUTION_PLANNER_ENVELOPE_HASH_MISMATCH"):
        GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
            mutated_grant,
            LiveCodegenAdapter(plan=plan, backend=backend),
        )
    assert backend.calls == 0


def test_protected_targets_fail_before_backend_invocation() -> None:
    task = runner_task(allowed_files=["scripts/runner_poll_github_tasks.py"])
    with pytest.raises(LiveCodegenBindingError, match="LIVE_CODEGEN_PROTECTED_TARGET"):
        compile_live_codegen_plan(task, source_task_ref="github:issue-4092", adapter_id="adapter:live-codegen")


@pytest.mark.parametrize(
    ("refs", "reason"),
    [
        ((), "EXECUTION_RESULT_TOUCHED_RESOURCES_REQUIRED"),
        (("repo:app/codegen_target.py", "repo:app/codegen_target.py"), "EXECUTION_RESULT_TOUCHED_RESOURCES_DUPLICATE"),
        (("protected:scripts/runner_poll_github_tasks.py",), "EXECUTION_RESULT_TOUCHED_RESOURCE_OUT_OF_SCOPE"),
        (("repo:outside.py",), "EXECUTION_RESULT_TOUCHED_RESOURCE_OUT_OF_SCOPE"),
    ],
)
def test_actual_touched_refs_must_be_non_empty_authorized_subset(refs, reason) -> None:
    plan, scheduler, ledger, grant = build_runtime()
    backend = Backend(
        LiveCodegenBackendResult(
            touched_resource_refs=refs,
            before_state_ref=grant.target_state_ref,
            after_state_ref="git:def",
            validation_status="PASS",
        )
    )
    with pytest.raises(GreenExecutionError, match=reason):
        GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
            grant,
            LiveCodegenAdapter(plan=plan, backend=backend),
        )
