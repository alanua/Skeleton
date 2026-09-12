from __future__ import annotations

from dataclasses import replace

import pytest

from core.runner_task import RunnerTask
from core.runner_vnext_adapters import ExecutionEnvelope
from core.runner_vnext_contracts import EffectClass, PrivacyClass
from core.runner_vnext_execution import GreenExecutionResult
from core.runner_vnext_execution_gate import GreenExecutionGrant, planner_envelope_hash
from core.runner_vnext_live_codegen import (
    PollerMechanicalCodegenBackend,
    RunnerVNextLiveCodegenError,
    compile_live_codegen_plan,
    run_authorized_live_codegen,
)

BASE_SHA = "2" * 40


def runner_task(
    *,
    allowed_files: tuple[str, ...] = ("app/a.py", "app/b.py"),
    privacy: str = "PUBLIC_SAFE_REPOSITORY_ONLY",
) -> RunnerTask:
    return RunnerTask.from_mapping({
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "branch": "runner/live-codegen-test",
        "base_sha": BASE_SHA,
        "task_kind": "code_edit",
        "payload": {"operation": "repair", "task": "bounded repair"},
        "requested_capabilities": [
            "repository_read",
            "repository_write_allowlisted",
            "test_execution",
        ],
        "allowed_files": list(allowed_files),
        "forbidden_actions": ["merge"],
        "validation_commands": [["python3", "-m", "pytest", "-q"]],
        "validation_timeout_seconds": 300,
        "expected_output": ["draft PR"],
        "privacy_boundary": privacy,
        "approval_reference": "chat:live-codegen-test",
        "idempotency_key": "live-codegen-test-v1",
    })


def grant_for(task: RunnerTask, *, source_task_ref: str = "issue:200") -> GreenExecutionGrant:
    plan = compile_live_codegen_plan(task, source_task_ref=source_task_ref)
    bound = plan.bound
    envelope = ExecutionEnvelope(
        operation_id=bound.operation_ir.operation_id,
        adapter_id=bound.binding.adapter_id,
        target_state_ref=bound.target_state_ref,
        fence_token=7,
        idempotency_key=bound.operation_ir.idempotency_key,
        resources=bound.operation_ir.resources,
        effects=bound.operation_ir.effects,
        effect_class=EffectClass.GREEN,
        policy_reason_code=bound.policy_decision.reason_code,
        required_capabilities=bound.universal_task.required_capabilities,
        privacy=PrivacyClass.PUBLIC_SAFE,
    )
    return GreenExecutionGrant(
        task_id=bound.universal_task.task_id,
        node_id="node:runner-1",
        node_generation=4,
        node_snapshot_hash="a" * 64,
        node_attestation_ref="attestation:runner-1-4",
        lane=bound.binding.lane.value,
        lease_scope_key=bound.lease_scope_key,
        fence_token=7,
        environment_policy_hash="b" * 64,
        operation_id=bound.operation_ir.operation_id,
        idempotency_key=bound.operation_ir.idempotency_key,
        adapter_id=bound.binding.adapter_id,
        target_state_ref=bound.target_state_ref,
        reservation_scope_hash="c" * 64,
        planner_envelope_hash=planner_envelope_hash(envelope),
        policy_reason_code=bound.policy_decision.reason_code,
        authorization_status="AUTHORIZED",
        environment={},
        planner_envelope=envelope,
    )


def result_for(
    grant: GreenExecutionGrant,
    *,
    resources: tuple[str, ...],
    outcome: str = "SUCCEEDED",
    before: str | None = None,
    after: str | None = None,
    rollback: bool = False,
) -> GreenExecutionResult:
    before_ref = before or grant.target_state_ref
    after_ref = after or ("git:" + "3" * 40 if outcome == "SUCCEEDED" else before_ref)
    return GreenExecutionResult(
        operation_id=grant.operation_id,
        idempotency_key=grant.idempotency_key,
        adapter_id=grant.adapter_id,
        target_state_ref=grant.target_state_ref,
        fence_token=grant.fence_token,
        resources=resources,
        effects=grant.planner_envelope.effects,
        outcome=outcome,
        reason_code="EXECUTION_PASS" if outcome == "SUCCEEDED" else "EXECUTION_FAILED",
        before_state_ref=before_ref,
        after_state_ref=after_ref,
        validation_status="PASS" if outcome == "SUCCEEDED" else "FAIL",
        rollback_requested=rollback,
    )


class Backend:
    def __init__(self, *, expected_task: RunnerTask, result: GreenExecutionResult) -> None:
        self.expected_task = expected_task
        self.result = result
        self.calls = 0
        self.source_task_ref: str | None = None

    def run_codegen(self, *, runner_task, source_task_ref, grant):
        self.calls += 1
        assert runner_task is self.expected_task
        self.source_task_ref = source_task_ref
        return self.result


def test_backend_receives_exact_runner_task_after_exact_binding_checks() -> None:
    task = runner_task()
    grant = grant_for(task)
    backend = Backend(
        expected_task=task,
        result=result_for(grant, resources=(grant.planner_envelope.resources[0],)),
    )
    receipt = run_authorized_live_codegen(
        runner_task=task,
        source_task_ref="issue:200",
        grant=grant,
        backend=backend,
    )
    assert backend.calls == 1
    assert backend.source_task_ref == "issue:200"
    assert receipt.result.resources == (grant.planner_envelope.resources[0],)
    assert receipt.publication_attempted is False


def test_different_source_ref_changes_identity_and_blocks_before_backend() -> None:
    task = runner_task()
    grant = grant_for(task, source_task_ref="issue:200")
    backend = Backend(
        expected_task=task,
        result=result_for(grant, resources=(grant.planner_envelope.resources[0],)),
    )
    with pytest.raises(RunnerVNextLiveCodegenError, match="LIVE_CODEGEN_GRANT_SCOPE_MISMATCH"):
        run_authorized_live_codegen(
            runner_task=task,
            source_task_ref="issue:201",
            grant=grant,
            backend=backend,
        )
    assert backend.calls == 0


def test_extra_capability_in_grant_blocks_before_backend() -> None:
    task = runner_task()
    grant = grant_for(task)
    widened_envelope = replace(
        grant.planner_envelope,
        required_capabilities=(
            *grant.planner_envelope.required_capabilities,
            "diagnostic_read",
        ),
    )
    widened_grant = replace(
        grant,
        planner_envelope=widened_envelope,
        planner_envelope_hash=planner_envelope_hash(widened_envelope),
    )
    backend = Backend(
        expected_task=task,
        result=result_for(grant, resources=(grant.planner_envelope.resources[0],)),
    )
    with pytest.raises(RunnerVNextLiveCodegenError, match="LIVE_CODEGEN_GRANT_SCOPE_MISMATCH"):
        run_authorized_live_codegen(
            runner_task=task,
            source_task_ref="issue:200",
            grant=widened_grant,
            backend=backend,
        )
    assert backend.calls == 0


def test_success_requires_truthful_nonempty_touched_subset() -> None:
    task = runner_task()
    grant = grant_for(task)
    backend = Backend(expected_task=task, result=result_for(grant, resources=()))
    with pytest.raises(
        RunnerVNextLiveCodegenError,
        match="EXECUTION_RESULT_TOUCHED_RESOURCES_REQUIRED",
    ):
        run_authorized_live_codegen(
            runner_task=task,
            source_task_ref="issue:200",
            grant=grant,
            backend=backend,
        )


def test_out_of_scope_or_duplicate_touched_refs_fail_closed() -> None:
    task = runner_task()
    grant = grant_for(task)
    for resources, reason in (
        (("repo:other.py",), "EXECUTION_RESULT_TOUCHED_RESOURCE_OUT_OF_SCOPE"),
        (
            (grant.planner_envelope.resources[0], grant.planner_envelope.resources[0]),
            "EXECUTION_RESULT_TOUCHED_RESOURCES_DUPLICATE",
        ),
    ):
        backend = Backend(expected_task=task, result=result_for(grant, resources=resources))
        with pytest.raises(RunnerVNextLiveCodegenError, match=reason):
            run_authorized_live_codegen(
                runner_task=task,
                source_task_ref="issue:200",
                grant=grant,
                backend=backend,
            )


def test_failed_no_mutation_may_report_zero_touched_resources() -> None:
    task = runner_task()
    grant = grant_for(task)
    backend = Backend(
        expected_task=task,
        result=result_for(
            grant,
            resources=(),
            outcome="FAILED",
            before=grant.target_state_ref,
            after=grant.target_state_ref,
        ),
    )
    receipt = run_authorized_live_codegen(
        runner_task=task,
        source_task_ref="issue:200",
        grant=grant,
        backend=backend,
    )
    assert receipt.result.resources == ()
    assert receipt.result.outcome == "FAILED"


def test_poller_backend_success_without_mutation_is_typed_fail() -> None:
    task = runner_task()
    grant = grant_for(task)
    mechanics_calls = 0
    validation_calls = 0

    def run_mechanics(_content, _workdir, exact_task):
        nonlocal mechanics_calls
        mechanics_calls += 1
        assert exact_task is task
        return 0, "RESULT: DONE"

    def run_validation(_command, _workdir):
        nonlocal validation_calls
        validation_calls += 1
        return 0, "ok"

    backend = PollerMechanicalCodegenBackend(
        task_content="bounded repair",
        workdir="/workspace",
        run_mechanics=run_mechanics,
        changed_files=lambda _workdir: (),
        run_validation_command=run_validation,
        workspace_state_ref=lambda _workdir: grant.target_state_ref,
        classify_mechanics_status=lambda _output, _code: "DONE",
    )
    receipt = run_authorized_live_codegen(
        runner_task=task,
        source_task_ref="issue:200",
        grant=grant,
        backend=backend,
    )
    assert mechanics_calls == 1
    assert validation_calls == 0
    assert receipt.result.outcome == "FAILED"
    assert receipt.result.validation_status == "FAIL"


def test_failed_changed_state_without_touched_resource_fails_closed() -> None:
    task = runner_task()
    grant = grant_for(task)
    backend = Backend(
        expected_task=task,
        result=result_for(
            grant,
            resources=(),
            outcome="FAILED",
            before=grant.target_state_ref,
            after="git:" + "4" * 40,
        ),
    )
    with pytest.raises(
        RunnerVNextLiveCodegenError,
        match="EXECUTION_RESULT_FAILED_MUTATION_REQUIRES_TOUCHED_RESOURCES",
    ):
        run_authorized_live_codegen(
            runner_task=task,
            source_task_ref="issue:200",
            grant=grant,
            backend=backend,
        )


def test_private_or_protected_task_is_rejected_before_backend() -> None:
    with pytest.raises(
        RunnerVNextLiveCodegenError,
        match="LIVE_CODEGEN_PUBLIC_REPOSITORY_PRIVACY_REQUIRED",
    ):
        compile_live_codegen_plan(
            runner_task(privacy="LOCAL_PRIVATE"),
            source_task_ref="issue:200",
        )

    with pytest.raises(
        RunnerVNextLiveCodegenError,
        match="LIVE_CODEGEN_PROTECTED_TARGET",
    ):
        compile_live_codegen_plan(
            runner_task(allowed_files=("core/runner_vnext_execution.py",)),
            source_task_ref="issue:200",
        )
