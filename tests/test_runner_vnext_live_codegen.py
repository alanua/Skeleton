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
    REPOSITORY_CODEGEN_ADAPTER_ID,
    RepositoryCodegenBackendResult,
    RepositoryCodegenBoundaryError,
    RepositoryCodegenExecutorAdapter,
    compile_repository_codegen_plan,
)
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot, RoutePlanner
from core.runner_vnext_scheduler import VNextScheduler


NOW = 100.0
BASE_SHA = "6d8e43e9c419e47038ca3296e936e7907f0be61b"


def runner_task(**overrides) -> RunnerTask:
    value = {
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "branch": "runner/vnext-source-bound-green-executor-v1",
        "base_sha": BASE_SHA,
        "task_kind": "code_edit",
        "payload": {"operation": "runner_vnext_source_bound_green_repository_executor_v1"},
        "requested_capabilities": [
            "repository_read",
            "repository_write_allowlisted",
            "test_execution",
        ],
        "allowed_files": [
            "core/runner_vnext_live_codegen.py",
            "tests/test_runner_vnext_live_codegen.py",
        ],
        "forbidden_actions": ["no live provider invocation"],
        "validation_commands": [["python3", "-m", "pytest", "-q", "tests/test_runner_vnext_live_codegen.py"]],
        "validation_timeout_seconds": 1800,
        "expected_output": ["focused tests PASS"],
        "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
        "approval_reference": "chat:20260912-runner-vnext-autonomous-completion",
        "idempotency_key": "runner-vnext-source-bound-green-executor-6d8e43e9-v1",
    }
    value.update(overrides)
    return RunnerTask.from_mapping(value)


def runtime_for(plan):
    nodes = NodeCapabilityRegistry()
    nodes.register(
        NodeCapabilitySnapshot(
            node_id="node:runner-1",
            generation=3,
            route_rank=1,
            capabilities=("repository_write_allowlisted", "repository_read", "test_execution"),
            supported_adapters=(REPOSITORY_CODEGEN_ADAPTER_ID,),
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
        {
            "lane:codegen": LaneEnvironmentPolicy(
                "lane:codegen",
                ("HOME", "PATH"),
                (),
                ("OPENROUTER_API_KEY", "BWS_ACCESS_TOKEN", "SKELETON_TG_BOT"),
                (),
            )
        }
    )
    scheduler = VNextScheduler(LaneLeaseStore(clock=lambda: NOW))
    ledger = OperationLedger()
    control = GreenControlPlane(
        route_planner=RoutePlanner(nodes),
        environment_registry=envs,
        scheduler=scheduler,
        adapter_planner=AdapterPlanner(
            {
                REPOSITORY_CODEGEN_ADAPTER_ID: AdapterManifest(
                    adapter_id=REPOSITORY_CODEGEN_ADAPTER_ID,
                    operation_kinds=("workspace_write",),
                    capabilities=("repository_write_allowlisted", "repository_read", "test_execution"),
                    allowed_effect_classes=(EffectClass.GREEN,),
                    resource_patterns=("repo:*",),
                    privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
                    privileged_pep_required=False,
                )
            }
        ),
        ledger=ledger,
    )
    handoff = control.prepare(
        task=plan.universal_task,
        operation=plan.operation,
        adapter_id=REPOSITORY_CODEGEN_ADAPTER_ID,
        lane=Lane.CODEGEN,
        lease_scope_key="repo:alanua/Skeleton:runner/vnext-source-bound-green-executor-v1",
        target_state_ref=plan.context.target_state_ref,
        ttl_seconds=30,
        now=NOW,
        parent_environment={"HOME": "/home/agent", "PATH": "/usr/bin", "OPENROUTER_API_KEY": "secret"},
        injected_environment={},
    )
    gate = GreenExecutionGate(
        nodes=nodes,
        environment_registry=envs,
        scheduler=scheduler,
        ledger=ledger,
        target_state_verifier=TargetVerifier(plan.context.target_state_ref),
    )
    return scheduler, ledger, gate.grant(handoff, now=NOW)


class TargetVerifier:
    def __init__(self, ref: str) -> None:
        self.ref = ref

    def current_ref(self, handoff) -> str:
        return self.ref


class FakeBackend:
    def __init__(self, produced: RepositoryCodegenBackendResult | None = None, *, raises: bool = False) -> None:
        self.produced = produced
        self.raises = raises
        self.calls = 0
        self.seen_task = None

    def execute(self, *, runner_task, context, fence_token):
        self.calls += 1
        self.seen_task = runner_task
        if self.raises:
            raise RuntimeError("private provider detail")
        return self.produced or RepositoryCodegenBackendResult(
            operation_id=context.operation_id,
            idempotency_key=context.idempotency_key,
            adapter_id=context.adapter_id,
            target_state_ref=context.target_state_ref,
            fence_token=fence_token,
            touched_resource_refs=(context.authorized_resource_refs[0],),
            before_state_ref=context.target_state_ref,
            after_state_ref="git:1111111111111111111111111111111111111111",
            validation_status="PASS",
        )


def test_compiler_binds_exact_runner_task_hash_to_vnext_identity_and_scope() -> None:
    task = runner_task()
    plan = compile_repository_codegen_plan(runner_task=task, source_task_ref="github-issue:4067")
    assert plan.context.task_envelope_hash
    assert plan.context.target_state_ref == f"git:{task.base_sha}"
    assert plan.universal_task.task_id == plan.context.task_id
    assert plan.operation.operation_id == plan.context.operation_id
    assert plan.operation.resources == tuple(f"repo:{path}" for path in task.allowed_files)
    assert "runner-task-envelope:" + plan.context.task_envelope_hash in plan.universal_task.operator_boundary_evidence


def test_changed_payload_allowed_files_validation_or_idempotency_changes_binding() -> None:
    base = compile_repository_codegen_plan(runner_task=runner_task(), source_task_ref="github-issue:4067")
    variants = [
        runner_task(payload={"operation": "runner_vnext_source_bound_green_repository_executor_v2"}),
        runner_task(allowed_files=["core/runner_vnext_live_codegen.py"]),
        runner_task(validation_commands=[["python3", "-m", "py_compile", "core/runner_vnext_live_codegen.py"]]),
        runner_task(idempotency_key="runner-vnext-source-bound-green-executor-6d8e43e9-v2"),
    ]
    bindings = {
        (
            compiled.context.task_id,
            compiled.context.operation_id,
            compiled.context.idempotency_key,
        )
        for variant in variants
        for compiled in [compile_repository_codegen_plan(runner_task=variant, source_task_ref="github-issue:4067")]
    }
    assert (base.context.task_id, base.context.operation_id, base.context.idempotency_key) not in bindings
    assert len(bindings) == len(variants)


def test_only_public_safe_code_edit_enters_repository_codegen_slice() -> None:
    with pytest.raises(RepositoryCodegenBoundaryError, match="REPOSITORY_CODEGEN_CODE_EDIT_REQUIRED"):
        compile_repository_codegen_plan(runner_task=runner_task(task_kind="diagnostic"), source_task_ref="github-issue:4067")
    with pytest.raises(RepositoryCodegenBoundaryError, match="REPOSITORY_CODEGEN_PUBLIC_SAFE_REQUIRED"):
        compile_repository_codegen_plan(runner_task=runner_task(privacy_boundary="LOCAL_PRIVATE"), source_task_ref="github-issue:4067")


def test_protected_allowed_file_fails_before_backend_invocation() -> None:
    task = runner_task(allowed_files=["scripts/runner_poll_github_tasks.py"])
    with pytest.raises(RepositoryCodegenBoundaryError, match="REPOSITORY_CODEGEN_PROTECTED_RESOURCE"):
        compile_repository_codegen_plan(runner_task=task, source_task_ref="github-issue:4067")


def test_grant_bound_adapter_invokes_backend_with_exact_validated_task_after_grant() -> None:
    task = runner_task()
    plan = compile_repository_codegen_plan(runner_task=task, source_task_ref="github-issue:4067")
    scheduler, ledger, grant = runtime_for(plan)
    backend = FakeBackend()
    receipt = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
        grant,
        RepositoryCodegenExecutorAdapter(backend=backend, runner_task=task, context=plan.context),
    )
    assert backend.calls == 1
    assert backend.seen_task is task
    assert receipt.touched_resource_refs == (plan.context.authorized_resource_refs[0],)
    assert receipt.terminal_status == "COMPLETED"


def test_swapped_runner_task_against_existing_grant_fails_before_backend() -> None:
    task = runner_task()
    plan = compile_repository_codegen_plan(runner_task=task, source_task_ref="github-issue:4067")
    scheduler, ledger, grant = runtime_for(plan)
    backend = FakeBackend()
    swapped = runner_task(payload={"operation": "mutated"})
    with pytest.raises(GreenExecutionError, match="REPOSITORY_CODEGEN_TASK_HASH_MISMATCH"):
        GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
            grant,
            RepositoryCodegenExecutorAdapter(backend=backend, runner_task=swapped, context=plan.context),
        )
    assert backend.calls == 0
    assert ledger.status(grant.idempotency_key) == "STARTED"


def test_mutated_grant_scope_or_target_fails_before_backend() -> None:
    task = runner_task()
    plan = compile_repository_codegen_plan(runner_task=task, source_task_ref="github-issue:4067")
    scheduler, ledger, grant = runtime_for(plan)
    backend = FakeBackend()
    mutated = replace(grant, target_state_ref="git:1111111111111111111111111111111111111111")
    with pytest.raises(GreenExecutionError, match="EXECUTION_GRANT_ENVELOPE_BINDING_MISMATCH"):
        GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
            mutated,
            RepositoryCodegenExecutorAdapter(backend=backend, runner_task=task, context=plan.context),
        )
    assert backend.calls == 0


def test_backend_output_outside_scope_fails_after_effect_and_needs_recovery() -> None:
    task = runner_task()
    plan = compile_repository_codegen_plan(runner_task=task, source_task_ref="github-issue:4067")
    scheduler, ledger, grant = runtime_for(plan)
    backend = FakeBackend(
        RepositoryCodegenBackendResult(
            operation_id=plan.context.operation_id,
            idempotency_key=plan.context.idempotency_key,
            adapter_id=plan.context.adapter_id,
            target_state_ref=plan.context.target_state_ref,
            fence_token=grant.fence_token,
            touched_resource_refs=("repo:outside.py",),
            before_state_ref=plan.context.target_state_ref,
            after_state_ref="git:1111111111111111111111111111111111111111",
            validation_status="PASS",
        )
    )
    with pytest.raises(GreenExecutionError, match="REPOSITORY_CODEGEN_TOUCHED_RESOURCE_OUT_OF_SCOPE"):
        GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
            grant,
            RepositoryCodegenExecutorAdapter(backend=backend, runner_task=task, context=plan.context),
        )
    assert backend.calls == 1
    assert ledger.status(grant.idempotency_key) == "STARTED"


def test_adapter_exception_stays_unresolved_for_recovery() -> None:
    task = runner_task()
    plan = compile_repository_codegen_plan(runner_task=task, source_task_ref="github-issue:4067")
    scheduler, ledger, grant = runtime_for(plan)
    backend = FakeBackend(raises=True)
    with pytest.raises(GreenExecutionError, match="EXECUTION_ADAPTER_EXCEPTION_NEEDS_RECOVERY") as exc:
        GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
            grant,
            RepositoryCodegenExecutorAdapter(backend=backend, runner_task=task, context=plan.context),
        )
    assert "private provider detail" not in str(exc.value)
    assert ledger.status(grant.idempotency_key) == "STARTED"
