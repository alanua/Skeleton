from __future__ import annotations

from dataclasses import replace
import json

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
    RepositoryCodegenCompileError,
    RepositoryCodegenExecutorAdapter,
    compile_repository_codegen_context,
)
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot, RoutePlanner
from core.runner_vnext_scheduler import VNextScheduler

NOW = 100.0
BASE_SHA = "e4312dad56c0728f616454602a82de2a2c664629"


def task_mapping(**overrides):
    value = {
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "branch": "runner/test",
        "base_sha": BASE_SHA,
        "task_kind": "code_edit",
        "payload": {"operation": "test"},
        "requested_capabilities": ("repository_read", "repository_write_allowlisted", "test_execution"),
        "allowed_files": ("core/example.py", "tests/test_example.py"),
        "forbidden_actions": ("no provider invocation",),
        "validation_commands": (("python3", "-m", "pytest", "-q", "tests/test_example.py"),),
        "validation_timeout_seconds": 1800,
        "expected_output": ("focused validation PASS",),
        "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
        "approval_reference": "chat:approval-1",
        "idempotency_key": "repo-codegen-test",
    }
    value.update(overrides)
    return value


class TargetVerifier:
    def __init__(self, target_state_ref: str) -> None:
        self.target_state_ref = target_state_ref

    def current_ref(self, handoff) -> str:
        return self.target_state_ref


class Backend:
    def __init__(self, produced: RepositoryCodegenBackendResult | None = None) -> None:
        self.produced = produced
        self.calls = 0
        self.seen_task_json: str | None = None

    def execute(self, task, context, grant):
        self.calls += 1
        self.seen_task_json = task.to_json()
        return self.produced or RepositoryCodegenBackendResult(
            operation_id=context.operation_id,
            idempotency_key=context.idempotency_key,
            adapter_id=context.adapter_id,
            target_state_ref=context.target_state_ref,
            fence_token=grant.fence_token,
            touched_resource_refs=("repo:core/example.py",),
            outcome="SUCCEEDED",
            reason_code="EXECUTION_PASS",
            before_state_ref=context.target_state_ref,
            after_state_ref="git:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            validation_status="PASS",
        )


def runtime(context):
    nodes = NodeCapabilityRegistry()
    nodes.register(NodeCapabilitySnapshot(
        node_id="node:runner-1", generation=3, route_rank=1,
        capabilities=("repository_read", "repository_write_allowlisted", "test_execution"),
        supported_adapters=(REPOSITORY_CODEGEN_ADAPTER_ID,), supported_lanes=(Lane.CODEGEN,),
        privacy_classes=(PrivacyClass.PUBLIC_SAFE,), resource_patterns=("repo:*",),
        observed_at=90.0, expires_at=200.0, attestation_ref="attestation:runner-1-3",
    ), now=NOW)
    envs = LaneEnvironmentPolicyRegistry({
        "lane:codegen": LaneEnvironmentPolicy(
            "lane:codegen", ("HOME", "PATH"), (), ("OPENROUTER_API_KEY",), (),
        )
    })
    manifest = AdapterManifest(
        adapter_id=REPOSITORY_CODEGEN_ADAPTER_ID,
        operation_kinds=("workspace_write",),
        capabilities=("repository_read", "repository_write_allowlisted", "test_execution"),
        allowed_effect_classes=(EffectClass.GREEN,),
        resource_patterns=("repo:*",),
        privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
        privileged_pep_required=False,
    )
    store = LaneLeaseStore(clock=lambda: NOW)
    scheduler = VNextScheduler(store)
    ledger = OperationLedger()
    control = GreenControlPlane(
        route_planner=RoutePlanner(nodes),
        environment_registry=envs,
        scheduler=scheduler,
        adapter_planner=AdapterPlanner({REPOSITORY_CODEGEN_ADAPTER_ID: manifest}),
        ledger=ledger,
    )
    handoff = control.prepare(
        task=context.universal_task,
        operation=context.operation,
        adapter_id=context.adapter_id,
        lane=Lane.CODEGEN,
        lease_scope_key="repo:branch-main",
        target_state_ref=context.target_state_ref,
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
        target_state_verifier=TargetVerifier(context.target_state_ref),
    )
    return scheduler, ledger, gate.grant(handoff, now=NOW)


def test_compiler_binds_exact_runner_task_hash_into_vnext_identity_and_target_state() -> None:
    context = compile_repository_codegen_context(task_mapping(), source_task_ref="issue:4081")
    runner_task_hash = context.runner_task_json_sha256
    assert runner_task_hash == __import__("hashlib").sha256(context.runner_task.to_json().encode("utf-8")).hexdigest()
    assert context.task_id.endswith(runner_task_hash)
    assert context.operation_id.endswith(runner_task_hash)
    assert context.idempotency_key.endswith(runner_task_hash)
    assert context.target_state_ref == f"git:{BASE_SHA}"
    assert context.resources == ("repo:core/example.py", "repo:tests/test_example.py")

    changed_payload = compile_repository_codegen_context(task_mapping(payload={"operation": "other"}), source_task_ref="issue:4081")
    changed_allowed = compile_repository_codegen_context(task_mapping(allowed_files=("core/example.py",)), source_task_ref="issue:4081")
    changed_validation = compile_repository_codegen_context(
        task_mapping(validation_commands=(("python3", "-m", "py_compile", "core/example.py"),)),
        source_task_ref="issue:4081",
    )
    assert len({runner_task_hash, changed_payload.runner_task_json_sha256, changed_allowed.runner_task_json_sha256, changed_validation.runner_task_json_sha256}) == 4


def test_live_codegen_rejects_non_public_code_edit_or_canonical_protected_targets_before_backend() -> None:
    with pytest.raises(RepositoryCodegenCompileError, match="REPOSITORY_CODEGEN_TASK_KIND_UNSUPPORTED"):
        compile_repository_codegen_context(task_mapping(task_kind="diagnostic"), source_task_ref="issue:4081")
    with pytest.raises(RepositoryCodegenCompileError, match="REPOSITORY_CODEGEN_PRIVACY_BOUNDARY_UNSUPPORTED"):
        compile_repository_codegen_context(task_mapping(privacy_boundary="PUBLIC_SAFE_AGGREGATE_ONLY"), source_task_ref="issue:4081")
    for protected in ("scripts/runner_poll_github_tasks.py", "core/runner_vnext_execution.py"):
        with pytest.raises(RepositoryCodegenCompileError, match="REPOSITORY_CODEGEN_PROTECTED_RESOURCE"):
            compile_repository_codegen_context(task_mapping(allowed_files=(protected,)), source_task_ref="issue:4081")


def test_grant_bound_adapter_calls_backend_with_exact_task_and_truthful_touched_subset() -> None:
    context = compile_repository_codegen_context(task_mapping(), source_task_ref="issue:4081")
    scheduler, ledger, grant = runtime(context)
    backend = Backend()
    receipt = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
        grant,
        RepositoryCodegenExecutorAdapter(context=context, backend=backend),
    )
    assert backend.calls == 1
    assert backend.seen_task_json == context.runner_task.to_json()
    assert receipt.touched_resource_refs == ("repo:core/example.py",)
    public = GreenExecutionLifecycle.public_projection(receipt)
    assert public["touched_resource_count"] == 1
    assert public["touched_resource_refs"] == ["repo:core/example.py"]


def test_swapped_compiled_runner_task_fails_before_backend_call() -> None:
    context = compile_repository_codegen_context(task_mapping(), source_task_ref="issue:4081")
    scheduler, ledger, grant = runtime(context)
    swapped = compile_repository_codegen_context(task_mapping(payload={"operation": "other"}), source_task_ref="issue:4081")
    backend = Backend()
    with pytest.raises(GreenExecutionError, match="EXECUTION_ADAPTER_EXCEPTION_NEEDS_RECOVERY"):
        GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
            grant,
            RepositoryCodegenExecutorAdapter(context=swapped, backend=backend),
        )
    assert backend.calls == 0
    assert ledger.status(grant.idempotency_key) == "STARTED"


def test_backend_touched_resources_are_fail_closed_for_outside_duplicate_or_zero_mutation() -> None:
    context = compile_repository_codegen_context(task_mapping(), source_task_ref="issue:4081")
    bad_results = (
        RepositoryCodegenBackendResult(context.operation_id, context.idempotency_key, context.adapter_id, context.target_state_ref, 1, ("repo:outside.py",), "SUCCEEDED", "EXECUTION_PASS", context.target_state_ref, "git:b", "PASS"),
        RepositoryCodegenBackendResult(context.operation_id, context.idempotency_key, context.adapter_id, context.target_state_ref, 1, ("repo:core/example.py", "repo:core/example.py"), "SUCCEEDED", "EXECUTION_PASS", context.target_state_ref, "git:b", "PASS"),
        RepositoryCodegenBackendResult(context.operation_id, context.idempotency_key, context.adapter_id, context.target_state_ref, 1, (), "SUCCEEDED", "EXECUTION_PASS", context.target_state_ref, "git:b", "PASS"),
    )
    for produced in bad_results:
        scheduler, ledger, grant = runtime(context)
        produced = replace(produced, fence_token=grant.fence_token)
        with pytest.raises(GreenExecutionError):
            GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(
                grant,
                RepositoryCodegenExecutorAdapter(context=context, backend=Backend(produced)),
            )


def test_compiler_accepts_already_validated_runner_task_without_reordering_drift() -> None:
    task = RunnerTask.from_mapping(task_mapping())
    context = compile_repository_codegen_context(task, source_task_ref="issue:4081")
    assert json.loads(context.runner_task.to_json()) == json.loads(task.to_json())
