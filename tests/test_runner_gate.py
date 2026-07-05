from __future__ import annotations

from dataclasses import replace

import pytest

from core.action_gate import ActionGateRequest
from core.gate_engine import GateResult
from core.runner_gate import (
    ROUTE_PRIVACY_BOUNDARIES,
    ROUTE_REQUIRED_CAPABILITIES,
    RunnerGate,
    RunnerGateContext,
)
from core.runner_task import RUNNER_TASK_SCHEMA, TASK_KINDS, RunnerTask


BASE_SHA = "433840879067c781be60cb5f1f371422aa5ae72e"
HEAD_SHA = "a" * 40
APPROVAL = "operator-chat-2026-07-05-slice3-1496"


def task(
    task_kind: str = "code_edit",
    *,
    capabilities: tuple[str, ...] | None = None,
    files: tuple[str, ...] = ("core/runner_gate.py",),
    privacy: str | None = None,
    payload: dict[str, object] | None = None,
) -> RunnerTask:
    defaults = {
        "code_edit": (
            "repository_read",
            "repository_write_allowlisted",
            "test_execution",
        ),
        "repository_maintenance": (
            "repository_read",
            "repository_maintenance",
        ),
        "private_memory": ("memory_gateway_read",),
        "diagnostic": ("diagnostic_read",),
        "loop_control": ("loop_control",),
        "publish": ("repository_read", "publish_pull_request"),
    }
    privacy_defaults = {
        "private_memory": "LOCAL_PRIVATE",
        "diagnostic": "PUBLIC_SAFE_AGGREGATE_ONLY",
        "loop_control": "PUBLIC_SAFE_AGGREGATE_ONLY",
    }
    return RunnerTask.from_mapping(
        {
            "schema": RUNNER_TASK_SCHEMA,
            "repo": "alanua/Skeleton",
            "branch": "runner/issue-1516",
            "base_sha": BASE_SHA,
            "task_kind": task_kind,
            "payload": payload or {"issue_number": 1516},
            "requested_capabilities": list(capabilities or defaults[task_kind]),
            "allowed_files": list(files),
            "forbidden_actions": ["no runtime deployment"],
            "validation_commands": [["python3", "-m", "pytest", "-q"]],
            "validation_timeout_seconds": 900,
            "expected_output": ["draft PR"],
            "privacy_boundary": privacy
            or privacy_defaults.get(task_kind, "PUBLIC_SAFE_REPOSITORY_ONLY"),
            "approval_reference": APPROVAL,
            "idempotency_key": "skeleton-runner-gate-slice-3-v1",
        }
    )


def plan(files: tuple[str, ...] = ("core/runner_gate.py",)) -> dict[str, object]:
    return {
        "schema": "skeleton.patch_plan.v1",
        "target_files": list(files),
        "change_type": "add",
        "reason": "implement approved Runner gate slice",
        "current_rule_read": True,
        "critique": "compose existing gates",
        "minimal_patch": "add shadow gate and tests",
        "verification": ["focused pytest", "full pytest"],
        "approval_required": True,
        "operator_approval": True,
    }


def context(
    selected: RunnerTask | None = None,
    *,
    files: tuple[str, ...] | None = None,
    capabilities: tuple[str, ...] | None = None,
    kinds: tuple[str, ...] = tuple(sorted(TASK_KINDS)),
    approvals: tuple[str, ...] = (APPROVAL,),
    protected_approvals: tuple[str, ...] = (),
    patch: dict[str, object] | None = None,
    action: ActionGateRequest | None = None,
    head_sha: str | None = None,
) -> RunnerGateContext:
    selected = selected or task()
    selected_files = files or selected.allowed_files
    selected_capabilities = capabilities or selected.requested_capabilities
    if patch is None and {
        "repository_write_allowlisted",
        "repository_maintenance",
        "memory_gateway_write",
        "loop_control",
        "publish_pull_request",
    }.intersection(selected.requested_capabilities):
        patch = plan(selected_files)
    return RunnerGateContext(
        repo=selected.repo,
        branch=selected.branch,
        base_sha=selected.base_sha,
        target_files=selected_files,
        available_capabilities=selected_capabilities,
        registered_task_kinds=kinds,
        approved_references=approvals,
        protected_approval_references=protected_approvals,
        patch_plan=patch,
        action_request=action,
        current_head_sha=head_sha,
    )


def test_code_edit_passes_patch_gate() -> None:
    selected = task()
    decision = RunnerGate().evaluate(selected, context(selected))
    assert decision.allowed
    assert decision.patch_gate_decision is not None
    assert decision.patch_gate_decision.result is GateResult.ALLOWED


def test_read_route_needs_no_patch_or_action_gate() -> None:
    selected = task("diagnostic")
    decision = RunnerGate().evaluate(selected, context(selected, patch=None))
    assert decision.allowed
    assert decision.patch_gate_decision is None
    assert decision.action_gate_decision is None


def test_missing_patch_plan_fails_closed() -> None:
    selected = task()
    decision = RunnerGate().evaluate(
        selected,
        replace(context(selected), patch_plan=None),
    )
    assert "PATCH_GATE_BLOCKED" in decision.reason_codes
    assert decision.patch_gate_decision is not None
    assert decision.patch_gate_decision.result is GateResult.BLOCKED_NO_PLAN


def test_patch_and_task_files_must_match_runtime_files() -> None:
    selected = task(files=("core/runner_gate.py", "tests/test_runner_gate.py"))
    runtime = context(
        selected,
        files=("core/runner_gate.py",),
        patch=plan(("tests/test_runner_gate.py",)),
    )
    decision = RunnerGate().evaluate(selected, runtime)
    assert "TARGET_FILES_MISMATCH" in decision.reason_codes
    assert "PATCH_PLAN_TARGET_MISMATCH" in decision.reason_codes


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("repo", "sample/Repo", "REPOSITORY_MISMATCH"),
        ("branch", "runner/other", "BRANCH_MISMATCH"),
        ("base_sha", "b" * 40, "BASE_SHA_MISMATCH"),
    ],
)
def test_runtime_identity_is_pinned(field: str, value: str, code: str) -> None:
    selected = task()
    runtime = replace(context(selected), **{field: value})
    assert code in RunnerGate().evaluate(selected, runtime).reason_codes


def test_repository_and_executor_must_be_registered() -> None:
    selected = task()
    gate = RunnerGate(registered_repositories=("sample/Repo",))
    runtime = context(
        selected,
        kinds=tuple(sorted(TASK_KINDS - {"code_edit"})),
    )
    decision = gate.evaluate(selected, runtime)
    assert "REPOSITORY_NOT_REGISTERED" in decision.reason_codes
    assert "EXECUTOR_NOT_REGISTERED" in decision.reason_codes


def test_route_and_runtime_capabilities_are_checked() -> None:
    selected = task(
        capabilities=("repository_read", "repository_write_allowlisted"),
    )
    runtime = context(selected, capabilities=("repository_read",))
    decision = RunnerGate().evaluate(selected, runtime)
    assert "MISSING_ROUTE_CAPABILITY" in decision.reason_codes
    assert "CAPABILITY_NOT_AVAILABLE" in decision.reason_codes


def test_privacy_and_general_approval_are_checked() -> None:
    selected = task("code_edit", privacy="PUBLIC_SAFE_AGGREGATE_ONLY")
    runtime = context(selected, approvals=("different-approval",))
    decision = RunnerGate().evaluate(selected, runtime)
    assert "PRIVACY_BOUNDARY_MISMATCH" in decision.reason_codes
    assert "APPROVAL_REFERENCE_NOT_APPROVED" in decision.reason_codes


def test_private_memory_read_accepts_local_boundary() -> None:
    selected = task("private_memory", privacy="LOCAL_PRIVATE")
    decision = RunnerGate().evaluate(selected, context(selected, patch=None))
    assert decision.allowed
    assert decision.patch_gate_decision is None


def test_protected_target_requires_protected_approval() -> None:
    protected_file = "core/action_gate.py"
    selected = task(files=(protected_file,))
    runtime = context(selected, patch=plan((protected_file,)))
    blocked = RunnerGate().evaluate(selected, runtime)
    assert "PROTECTED_RESOURCE_APPROVAL_MISSING" in blocked.reason_codes
    allowed = RunnerGate().evaluate(
        selected,
        replace(runtime, protected_approval_references=(APPROVAL,)),
    )
    assert allowed.allowed


def publish_task() -> RunnerTask:
    return task(
        "publish",
        payload={"issue_number": 1516, "pr_number": 1517},
    )


def publish_action(*, approved: bool = True) -> ActionGateRequest:
    return ActionGateRequest(
        action_type="merge_pull_request",
        repo="alanua/Skeleton",
        pr_number=1517,
        expected_head_sha=HEAD_SHA,
        expected_files=("core/runner_gate.py",),
        user_approved=approved,
    )


def test_publish_passes_patch_and_action_gates() -> None:
    selected = publish_task()
    runtime = context(
        selected,
        action=publish_action(),
        head_sha=HEAD_SHA,
    )
    decision = RunnerGate().evaluate(selected, runtime)
    assert decision.allowed
    assert decision.action_gate_decision is not None
    assert decision.action_gate_decision.status == "allowed"


def test_publish_requires_action_request_and_current_head() -> None:
    selected = publish_task()
    missing_action = RunnerGate().evaluate(selected, context(selected))
    assert "ACTION_GATE_REQUIRED" in missing_action.reason_codes
    missing_head = RunnerGate().evaluate(
        selected,
        context(selected, action=publish_action()),
    )
    assert "CURRENT_HEAD_SHA_REQUIRED" in missing_head.reason_codes


def test_action_gate_and_cross_contract_mismatches_fail_closed() -> None:
    selected = publish_task()
    action = ActionGateRequest(
        action_type="merge_pull_request",
        repo="sample/Repo",
        pr_number=999,
        expected_head_sha="b" * 40,
        expected_files=("tests/test_runner_gate.py",),
        user_approved=False,
    )
    decision = RunnerGate().evaluate(
        selected,
        context(selected, action=action, head_sha=HEAD_SHA),
    )
    expected = {
        "ACTION_GATE_BLOCKED",
        "ACTION_REPOSITORY_MISMATCH",
        "ACTION_FILES_MISMATCH",
        "ACTION_HEAD_SHA_MISMATCH",
        "ACTION_PR_MISMATCH",
    }
    assert expected.issubset(decision.reason_codes)


def test_invalid_inputs_and_incomplete_configuration_fail_closed() -> None:
    gate = RunnerGate()
    assert gate.evaluate({}, context()).reason_codes == ("INVALID_RUNNER_TASK",)
    assert gate.evaluate(task(), {}).reason_codes == ("INVALID_GATE_CONTEXT",)

    route_caps = dict(ROUTE_REQUIRED_CAPABILITIES)
    route_caps.pop("publish")
    with pytest.raises(ValueError):
        RunnerGate(route_required_capabilities=route_caps)

    route_privacy = dict(ROUTE_PRIVACY_BOUNDARIES)
    route_privacy.pop("diagnostic")
    with pytest.raises(ValueError):
        RunnerGate(route_privacy_boundaries=route_privacy)
