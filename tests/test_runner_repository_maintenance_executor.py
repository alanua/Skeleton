from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from core.home_edge.action import FIXED_BASELINE_PROFILE, FIXED_TARGET_ID
from core.runner_repository_maintenance_executor import (
    APPROVED_PR_MERGE_TASK_ID,
    REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID,
    ApprovedPrMergeRequest,
    RepositoryMaintenanceExecutor,
    RepositoryMaintenanceExecutorError,
)
from core.runner_task import RUNNER_TASK_SCHEMA, RunnerTask


def task(payload: dict[str, object], *, allowed_files: list[str] | None = None) -> RunnerTask:
    return RunnerTask.from_mapping(
        {
            "schema": RUNNER_TASK_SCHEMA,
            "repo": "alanua/Skeleton",
            "branch": "runner/issue-2191",
            "base_sha": "1" * 40,
            "task_kind": "repository_maintenance",
            "payload": payload,
            "requested_capabilities": ["repository_read", "repository_maintenance"],
            "allowed_files": allowed_files or ["core/example.py"],
            "forbidden_actions": ["no live merge in tests"],
            "validation_commands": [["python3", "-m", "pytest", "-q"]],
            "validation_timeout_seconds": 900,
            "expected_output": ["bounded receipt"],
            "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
            "approval_reference": "operator-approved-merge",
            "idempotency_key": "test-key",
        }
    )


def pr_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "number": 2193,
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "a" * 40,
        "files": [{"path": "core/example.py"}],
        "operatorApprovals": [
            {
                "approval_reference": "operator-approved-merge",
                "action": "merge_pull_request",
                "pr_number": 2193,
                "expected_head_sha": "a" * 40,
                "expected_files": ["core/example.py"],
            }
        ],
    }
    state.update(overrides)
    return state


def test_approved_pr_merge_runs_bounded_merge_before_done() -> None:
    merged: list[ApprovedPrMergeRequest] = []

    executor = RepositoryMaintenanceExecutor(
        pr_state_reader=lambda pr_number: pr_state(number=pr_number),
        merge_runner=lambda request: merged.append(request)
        or {
            "status": "merged",
            "pr_number": request.pr_number,
            "head_sha": request.expected_head_sha,
            "merge_executed": True,
        },
    )

    report = executor.execute(
        task(
            {
                "maintenance_task_id": APPROVED_PR_MERGE_TASK_ID,
                "pr_number": 2193,
                "expected_head_sha": "a" * 40,
            }
        )
    )

    assert report.startswith("DONE:")
    assert "merge_side_effect=confirmed" in report
    assert len(merged) == 1


def test_approved_pr_merge_blocks_when_merge_runner_does_not_confirm_side_effect() -> None:
    executor = RepositoryMaintenanceExecutor(
        pr_state_reader=lambda _pr_number: pr_state(),
        merge_runner=lambda _request: {
            "status": "merged",
            "pr_number": 2193,
            "head_sha": "a" * 40,
            "merge_executed": False,
        },
    )

    report = executor.execute(
        task(
            {
                "maintenance_task_id": APPROVED_PR_MERGE_TASK_ID,
                "pr_number": 2193,
                "expected_head_sha": "a" * 40,
            }
        )
    )

    assert report.startswith("BLOCKED:")
    assert "reason=merge_side_effect_not_confirmed" in report


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (pr_state(isDraft=True), "pr_is_draft"),
        (pr_state(headRefOid="b" * 40), "head_sha_mismatch"),
        (pr_state(files=[{"path": "other.py"}]), "changed_files_mismatch"),
        (pr_state(operatorApprovals=[]), "operator_approval_missing"),
    ],
)
def test_approved_pr_merge_fail_closed_validation(state: Mapping[str, Any], reason: str) -> None:
    executor = RepositoryMaintenanceExecutor(
        pr_state_reader=lambda _pr_number: state,
        merge_runner=lambda _request: {"merge_executed": True},
    )

    report = executor.execute(
        task(
            {
                "maintenance_task_id": APPROVED_PR_MERGE_TASK_ID,
                "pr_number": 2193,
                "expected_head_sha": "a" * 40,
            }
        )
    )

    assert report.startswith("BLOCKED:")
    assert f"reason={reason}" in report


def test_extra_repository_maintenance_payload_field_fails_before_side_effect() -> None:
    called = False

    def merge_runner(_request: ApprovedPrMergeRequest) -> Mapping[str, Any]:
        nonlocal called
        called = True
        return {}

    executor = RepositoryMaintenanceExecutor(
        pr_state_reader=lambda _pr_number: pr_state(),
        merge_runner=merge_runner,
    )

    with pytest.raises(RepositoryMaintenanceExecutorError) as excinfo:
        executor.execute(
            task(
                {
                    "maintenance_task_id": APPROVED_PR_MERGE_TASK_ID,
                    "pr_number": 2193,
                    "expected_head_sha": "a" * 40,
                    "command": "gh pr merge 2193",
                }
            )
        )

    assert excinfo.value.reason_code == "UNKNOWN_REPOSITORY_MAINTENANCE_PAYLOAD_FIELD"
    assert called is False


def test_remote_read_only_diagnostic_executor_requires_de_pc_baseline() -> None:
    def home_edge_execute(_request: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "status": "completed",
            "target_id": FIXED_TARGET_ID,
            "baseline_profile": FIXED_BASELINE_PROFILE,
            "aggregate": {
                "classes": {"os": "windows"},
                "counts": {"checks": 2},
                "booleans": {"baseline": True},
            },
            "reason_codes": ["windows_baseline_completed"],
        }

    executor = RepositoryMaintenanceExecutor(home_edge_execute=home_edge_execute)

    report = executor.execute(
        task({"maintenance_task_id": REMOTE_READ_ONLY_DIAGNOSTIC_TASK_ID})
    )

    assert report.startswith("DONE:")
    assert "target_id=DE-PC" in report
    assert "baseline_completed=true" in report
    assert "private_details=private_runtime_artifact_only" in report
