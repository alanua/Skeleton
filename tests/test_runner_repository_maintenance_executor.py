from __future__ import annotations

from collections.abc import Mapping

import pytest

from core.runner_executor import RunnerExecutorError
from core.runner_executor_registry import RunnerExecutorRegistry
from core.runner_repository_maintenance_executor import (
    REPOSITORY_MAINTENANCE_REQUEST_SCHEMA,
    RepositoryMaintenanceExecutor,
)
from core.runner_task import RunnerTask


HEAD_SHA = "a" * 40
CALLBACK_DIGEST = "0123456789ab"


def _task(payload: Mapping[str, object], *, privacy: str = "PUBLIC_SAFE_REPOSITORY_ONLY") -> RunnerTask:
    return RunnerTask.from_mapping(
        {
            "schema": "skeleton.runner_task.v1",
            "repo": "alanua/Skeleton",
            "branch": "runner/issue-2191",
            "base_sha": "b" * 40,
            "task_kind": "repository_maintenance",
            "payload": dict(payload),
            "requested_capabilities": ["repository_read", "repository_maintenance"],
            "allowed_files": ["core/runner_executor_registry.py"],
            "forbidden_actions": ["no live merge"],
            "validation_commands": [["python3", "-m", "pytest", "-q"]],
            "validation_timeout_seconds": 1800,
            "expected_output": ["bounded maintenance receipt"],
            "privacy_boundary": privacy,
            "approval_reference": "EXPLICIT_PROTECTED_RUNNER_REPAIR_TEST_MERGE_RUNTIME_SYNC_20260715",
            "idempotency_key": "repository-maintenance-test",
        }
    )


def _merge_payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": REPOSITORY_MAINTENANCE_REQUEST_SCHEMA,
        "operation": "approved_pr_merge",
        "repository": "alanua/Skeleton",
        "pr_number": 123,
        "approved_head_sha": HEAD_SHA,
        "reviewed_files": ["core/runner_executor_registry.py"],
        "approval_source": "signed_telegram_callback",
        "callback_digest": CALLBACK_DIGEST,
        "merge_action": "squash",
    }
    payload.update(updates)
    return payload


def _pr_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "number": 123,
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "headRefOid": HEAD_SHA,
        "files": [{"path": "core/runner_executor_registry.py"}],
        "comments": [
            {
                "body": "\n".join(
                    (
                        "Verified approval record: signed_telegram_callback",
                        f"Verified head SHA: {HEAD_SHA}",
                        f"Callback digest: {CALLBACK_DIGEST}",
                    )
                )
            }
        ],
    }
    state.update(updates)
    return state


def test_approved_pr_merge_validates_exact_scope_and_reports_dry_run_command() -> None:
    executor = RepositoryMaintenanceExecutor(pr_state_reader=lambda _pr: _pr_state())
    report = executor.execute(_task(_merge_payload()))

    assert report.startswith("DONE:")
    assert "operation=approved_pr_merge" in report
    assert f"approved_head_sha={HEAD_SHA}" in report
    assert "changed_files=core/runner_executor_registry.py" in report
    assert "merge_command=gh_pr_merge_squash_match_head" in report
    assert "external_side_effects_executed=false" in report


def test_approved_pr_merge_blocks_changed_file_scope_mismatch_before_merge() -> None:
    calls: list[tuple[str, ...]] = []
    executor = RepositoryMaintenanceExecutor(
        pr_state_reader=lambda _pr: _pr_state(files=[{"path": "README.md"}]),
        merge_runner=lambda command: (calls.append(tuple(command)) or (0, "")),
    )

    report = executor.execute(_task(_merge_payload()))

    assert report.startswith("BLOCKED:")
    assert "reason=changed_files_scope_mismatch" in report
    assert calls == []


def test_repository_maintenance_rejects_extra_payload_fields_before_side_effects() -> None:
    executor = RepositoryMaintenanceExecutor(
        pr_state_reader=lambda _pr: (_ for _ in ()).throw(AssertionError("must not read PR"))
    )
    with pytest.raises(RunnerExecutorError) as exc_info:
        executor.execute(_task(_merge_payload(shell="echo unsafe")))

    assert exc_info.value.reason_code == "UNKNOWN_REPOSITORY_MAINTENANCE_OPERATION_FIELD"


def test_remote_read_only_diagnostic_uses_home_edge_action_boundary() -> None:
    def action(request: Mapping[str, object]) -> dict[str, object]:
        assert request == {
            "schema": "skeleton.home_edge.action.v1",
            "operation": "remote_read_only_diagnostic",
            "node_id": "home-edge-01",
            "probe_profile": "de_pc_read_only_v1",
        }
        return {
            "status": "observed",
            "node_id": "home-edge-01",
            "probe_profile": "de_pc_read_only_v1",
            "aggregate_classes": ["gateway", "route", "tailscale", "modem"],
            "counts": {"diagnostic_count": 1},
            "booleans": {
                "usb_modem_health_required": False,
                "gateway_ready": True,
                "route_unchanged": True,
                "tailscale_healthy": True,
            },
            "reason_code": "healthy_transport",
            "gateway_status": "ready",
            "route_status": "unchanged",
            "tailscale_status": "healthy",
            "modem_status": "optional_not_attached",
        }

    executor = RepositoryMaintenanceExecutor(home_edge_action=action)
    report = executor.execute(
        _task(
            {
                "schema": REPOSITORY_MAINTENANCE_REQUEST_SCHEMA,
                "operation": "remote_read_only_diagnostic",
                "node_id": "home-edge-01",
                "probe_profile": "de_pc_read_only_v1",
            },
            privacy="PUBLIC_SAFE_AGGREGATE_ONLY",
        )
    )

    assert report.startswith("DONE:")
    assert "aggregate_classes=gateway,route,tailscale,modem" in report
    assert "diagnostic_count=1" in report
    assert "usb_modem_health_required=false" in report
    assert "reason_code=healthy_transport" in report


def test_repository_maintenance_executor_registered_by_capability_gate() -> None:
    registry = RunnerExecutorRegistry([RepositoryMaintenanceExecutor(pr_state_reader=lambda _pr: _pr_state())])
    task = _task(_merge_payload())

    assert registry.lookup("repository_maintenance").required_capabilities == (
        "repository_maintenance",
        "repository_read",
    )
    assert registry.dispatch(task).startswith("DONE:")


def test_repository_maintenance_registry_blocks_missing_capability() -> None:
    registry = RunnerExecutorRegistry([RepositoryMaintenanceExecutor(pr_state_reader=lambda _pr: _pr_state())])
    task = RunnerTask.from_mapping(
        {
            **_task(_merge_payload()).to_mapping(),
            "requested_capabilities": ["repository_read"],
        }
    )

    with pytest.raises(RunnerExecutorError) as exc_info:
        registry.dispatch(task)

    assert exc_info.value.reason_code == "MISSING_EXECUTOR_CAPABILITY"
