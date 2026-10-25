from __future__ import annotations

import csv
import hashlib
import inspect
import json
import os
import re
import sqlite3
import time
import urllib.parse
from pathlib import Path
from unittest import mock

import pytest

from scripts import runner_poll_github_tasks as runner
from scripts import telegram_callback_poller as callback_poller
from core.secret_store import SecretReference
from core.home_edge import esp_lab_activation
from core.home_edge import post_migration_reconcile


ORIGINAL_GRAPHIFY_RESOLVE_USER_SCRIPT = runner._graphify_resolve_user_script
ORIGINAL_GRAPHIFY_RESOLVE_GRAPHIFY_CLI = runner._graphify_resolve_graphify_cli

HEAD_SHA = "a" * 40
PR_URL = "https://github.com/alanua/Skeleton/pull/123"
DONE_REPORT = f"""DONE: Codex completed successfully and produced file changes.

Changed files:
- scripts/runner_poll_github_tasks.py
- docs/TELEGRAM_APPROVAL_BUTTONS.md

Pytest output:
```
99 passed
```

Commit: {HEAD_SHA}
Draft PR: {PR_URL}"""
CALLBACK_HMAC_SECRET = "runner-callback-hmac-test-secret"
CALLBACK_DIGEST = runner.hmac.new(
    CALLBACK_HMAC_SECRET.encode("utf-8"),
    f"tpr1:approve:p123:{HEAD_SHA[:8]}".encode("ascii"),
    runner.hashlib.sha256,
).hexdigest()[:12]
HOME_EDGE_EXEC_HMAC_ENV = "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"
SYNTHETIC_HOME_EDGE_EXEC_HMAC = "synthetic-home-edge-exec-hmac-marker"


@pytest.fixture(autouse=True)
def _isolate_runner_vnext_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(runner.RUNNER_VNEXT_MODE_ENV, raising=False)


def _media_bootstrap_issue_body(expected_sha: str = HEAD_SHA) -> str:
    return "\n".join(
        (
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {runner.HOME_EDGE_01_DEBIAN_MEDIA_BOOTSTRAP_V1}",
            f"Repository: {runner.REPO}",
            f"Expected Main SHA: {expected_sha}",
            "Operator Approval: EXPLICIT_FINISH_DEBIAN_MEDIA_NODE_20260805",
            "Target: home-edge-01",
        )
    )


def _media_bootstrap_receipt() -> dict[str, object]:
    return {
        "maintenance_task_id": runner.HOME_EDGE_01_DEBIAN_MEDIA_BOOTSTRAP_V1,
        "os_identity_status": "verified",
        "node_identity_status": "verified",
        "reboot_guard_status": "not_present",
        "reboot_performed": False,
        "packages_required_count": 20,
        "packages_preexisting_count": 20,
        "packages_added_count": 0,
        "package_status": "installed",
        "display_manager_status": "service_active",
        "autologin_status": "configured",
        "pipewire_status": "session_ready",
        "vaapi_status": "physical_pending",
        "mpv_status": "configured",
        "chromium_status": "configured",
        "ssh_status": "service_active",
        "gateway_postcheck_status": "ok",
        "physical_audio_status": "physical_pending",
        "physical_video_status": "physical_pending",
        "rollback_ready": True,
        "rollback_applied": False,
        "mutation_executor_receipt_hash": "b" * 64,
        "final_postcheck_receipt_hash": "c" * 64,
        "audit_receipt_hash": "a" * 64,
        "stable_reason": "already_complete",
        "success_criteria": "met",
    }


def _post_migration_issue_body(expected_sha: str = HEAD_SHA) -> str:
    return "\n".join(
        (
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {runner.HOME_EDGE_01_POST_MIGRATION_RECONCILE_V1}",
            f"Repository: {runner.REPO}",
            f"Expected Main SHA: {expected_sha}",
            "Operator Approval: EXPLICIT_RECONCILE_HOME_EDGE_POST_MIGRATION_20260808",
            "Target: home-edge-01",
        )
    )


def _post_migration_receipt() -> dict[str, object]:
    return {
        "maintenance_task_id": runner.HOME_EDGE_01_POST_MIGRATION_RECONCILE_V1,
        "os_identity_status": "verified",
        "node_identity_status": "verified",
        "boot_id_unchanged": True,
        "registry_status": "healthy",
        "gallery_status": "repaired_healthy",
        "brother_status": "healthy",
        "aggregate_status": "healthy",
        "cast_status": "healthy",
        "pointer_status": "healthy",
        "watchdog_status": "healthy",
        "watchdog_critical_count": 0,
        "watchdog_warning_count": 0,
        "refresh_count": 1,
        "aggregate_source_repaired": True,
        "stale_before_count": 2,
        "stale_after_count": 0,
        "changed_file_count": 2,
        "system_failed_unit_count": 0,
        "user_failed_unit_count": 0,
        "current_brother_service_status": "active",
        "current_brother_guard_timer_status": "active",
        "rollback_ready": True,
        "rollback_applied": False,
        "mutation_executor_receipt_hash": "b" * 64,
        "audit_receipt_hash": "a" * 64,
        "stable_reason": "completed",
        "success_criteria": "met",
        "canonical_memory_post_step": "home_edge_audit_persist_v1",
    }


def _media_source_snapshot_issue_body(expected_sha: str = HEAD_SHA) -> str:
    return "\n".join(
        (
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {runner.HOME_EDGE_01_MEDIA_SOURCE_SNAPSHOT_V1}",
            f"Repository: {runner.REPO}",
            f"Expected Main SHA: {expected_sha}",
            "Target: home-edge-01",
        )
    )


def _media_source_snapshot_receipt() -> dict[str, object]:
    return {
        "maintenance_task_id": runner.HOME_EDGE_01_MEDIA_SOURCE_SNAPSHOT_V1,
        "source_identity": "verified",
        "source_version_marker": "v63",
        "source_bytes": 1234,
        "source_sha256": "c" * 64,
        "python_parse_status": "ok",
        "video_route_present": True,
        "health_route_present": True,
        "private_artifact_written": True,
        "private_artifact_hash_matches": True,
        "executor_receipt_hash": "b" * 64,
        "stable_reason": "completed",
        "success_criteria": "met",
    }


def _codex_primary_health_issue_body(expected_sha: str = HEAD_SHA) -> str:
    return "\n".join(
        (
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {runner.RUNNER_CODEX_PRIMARY_HEALTH_PROBE}",
            f"Repository: {runner.REPO}",
            f"Expected Main SHA: {expected_sha}",
        )
    )


def _esp_lab_stage1_signer_install_body(
    *,
    expected_main_sha: str = runner.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_APPROVED_MAIN_SHA,
    repository: str = runner.REPO,
    target: str = "runner-controller",
    approval: str = runner.HOME_EDGE_ESP_LAB_STAGE1_SIGNER_OPERATOR_APPROVAL,
    extra: tuple[str, ...] = (),
) -> str:
    return "\n".join(
        (
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {runner.HOME_EDGE_01_ESP_LAB_STAGE1_SIGNER_INSTALL_V1}",
            f"Repository: {repository}",
            f"Expected Main SHA: {expected_main_sha}",
            f"Target: {target}",
            f"Operator Approval: {approval}",
            *extra,
        )
    )


def _esp_lab_stage1_activation_body(
    *,
    task_id: str = runner.HOME_EDGE_01_ESP_LAB_STAGE1_ACTIVATION_V1,
    expected_main_sha: str = HEAD_SHA,
    repository: str = runner.REPO,
    target: str = "home-edge-01",
    approval: str = esp_lab_activation.OPERATOR_APPROVAL_REF,
    extra: tuple[str, ...] = (),
) -> str:
    return "\n".join(
        (
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {task_id}",
            f"Repository: {repository}",
            f"Expected Main SHA: {expected_main_sha}",
            f"Target: {target}",
            f"Operator Approval: {approval}",
            *extra,
        )
    )


def _merge_issue_body(
    *,
    pr_number: int = 123,
    head_sha: str = HEAD_SHA,
    action: str = "squash",
    approval_source: str = "signed_telegram_callback",
) -> str:
    return "\n".join(
        (
            f"Mode: {runner.TELEGRAM_APPROVED_PR_MERGE_MODE}",
            f"Repository: {runner.REPO}",
            f"Pull Request: {pr_number}",
            f"Approved Head SHA: {head_sha}",
            f"Merge Action: {action}",
            f"Approval Source: {approval_source}",
            f"Callback Digest: {CALLBACK_DIGEST}",
        )
    )


def _merge_comments() -> list[dict[str, str]]:
    return [
        {
            "body": (
                "Operator event record (Telegram callback stage 1)\n"
                f"Repository: {runner.REPO}\n"
                "Pull request: #123\n"
                "Action: telegram_approve\n"
                f"Head marker: {HEAD_SHA[:8]}\n"
                f"Callback digest: {CALLBACK_DIGEST}\n"
                "Result: recorded\n"
                "Verified approval record: signed_telegram_callback\n"
                f"Verified head SHA: {HEAD_SHA}\n"
            )
        }
    ]


def _merge_pr_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "number": 123,
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "headRefOid": HEAD_SHA,
        "comments": _merge_comments(),
    }
    state.update(updates)
    return state


def test_poll_once_discovers_native_registered_repo_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    skeleton_source = runner.RunnerVNextQueueSource("skeleton", runner.REPO)
    lavalamp_source = runner.RunnerVNextQueueSource("lavalamp", "alanua/Lavalamp")
    native_issue = {
        "number": 7,
        "title": "Native",
        "body": "```task\nDo it.\n```",
        "state": "OPEN",
        "closed": False,
        "url": "https://github.com/alanua/Lavalamp/issues/7",
        "labels": ["runner:ready"],
    }

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv(runner.RUNNER_MODE_ENV, raising=False)
    monkeypatch.delenv(runner.RUNNER_SHADOW_MODE_ENV, raising=False)
    monkeypatch.setenv(runner.RUNNER_VNEXT_MODE_ENV, "shadow")
    monkeypatch.setattr(
        runner,
        "registered_runner_queue_sources",
        lambda _project_tree: (skeleton_source, lavalamp_source),
    )
    monkeypatch.setattr(runner, "load_runner_project_tree", lambda: {"projects": {}})
    monkeypatch.setattr(runner, "get_ready_issues", lambda: [])
    monkeypatch.setattr(runner, "reconcile_scheduler_on_poll", lambda: None)
    monkeypatch.setattr(
        runner, "reconcile_terminal_issues_active_execution_labels", lambda: 0
    )
    monkeypatch.setattr(runner, "self_heal_run_now_queue_intake", lambda: 0)

    commands: list[list[str]] = []

    def run_command(command: list[str], **_kwargs: object) -> tuple[int, str]:
        commands.append(command)
        assert command[command.index("--repo") + 1] == "alanua/Lavalamp"
        return 0, json.dumps([native_issue])

    with mock.patch.object(runner, "run_command", side_effect=run_command), mock.patch.object(
        runner, "process_issue"
    ) as process_issue:
        count = runner.poll_once(workdir="/coordinator")

    assert count == 1
    assert process_issue.call_args_list == [
        mock.call(
            native_issue,
            workdir="/coordinator",
            source_repository="alanua/Lavalamp",
        )
    ]
    assert commands


def test_native_registered_repo_issue_defaults_target_to_source_repo() -> None:
    task, reason = runner.extract_runner_task(
        "```task\nDo it.\n```",
        default_repository="alanua/Lavalamp",
    )

    assert reason is None
    assert task is not None
    assert task.target_project == "lavalamp"
    assert task.target_repository == "alanua/Lavalamp"


def test_native_registered_repo_worktree_identity_does_not_collide_with_skeleton_control_issue() -> None:
    skeleton_control_path = runner.target_repository_issue_worktree_path(
        "alanua/Lavalamp",
        7,
    )
    native_path = runner.target_repository_issue_worktree_path(
        "alanua/Lavalamp",
        7,
        source_repository="alanua/Lavalamp",
    )

    assert native_path != skeleton_control_path
    assert skeleton_control_path.name == "issue-7"
    assert native_path.name == "issue-alanua-Lavalamp-7"
    assert runner.issue_branch(7) == "runner/issue-7"
    assert (
        runner.issue_branch(7, source_repository="alanua/Lavalamp")
        == "runner/alanua-Lavalamp-issue-7"
    )


def test_process_issue_routes_comments_and_labels_to_source_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = {
        "number": 7,
        "title": "Native",
        "body": "No task yet",
        "state": "OPEN",
        "closed": False,
    }
    commands: list[list[str]] = []

    def run_command(command: list[str], **_kwargs: object) -> tuple[int, str]:
        commands.append(command)
        if command[:3] == ["gh", "issue", "view"]:
            return 0, json.dumps({"labels": [{"name": runner.LABEL_READY}]})
        return 0, ""

    monkeypatch.setattr(runner, "record_runner_executor_result", lambda *args: None)
    monkeypatch.setattr(runner, "notify_task_finished", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner, "maybe_replenish_runner_queue_after_completion", lambda: False
    )

    with mock.patch.object(runner, "run_command", side_effect=run_command):
        runner.process_issue(issue, source_repository="alanua/Lavalamp")

    issue_commands = [
        command for command in commands if command[:2] == ["gh", "issue"]
    ]
    assert issue_commands
    assert {
        command[command.index("--repo") + 1]
        for command in issue_commands
        if "--repo" in command
    } == {"alanua/Lavalamp"}


def _inspect_pr_issue_body(
    *,
    pr_number: int | str | None = 123,
    expected_head_sha: str | None = HEAD_SHA,
    repository: str | None = runner.REPO,
) -> str:
    lines = [
        f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
        f"Maintenance Task ID: {runner.INSPECT_PR_MERGEABILITY}",
        f"Repository: {repository}",
    ]
    if pr_number is not None:
        lines.append(f"Pull Request: {pr_number}")
    if expected_head_sha is not None:
        lines.append(f"Expected Head SHA: {expected_head_sha}")
    return "\n".join(lines)


def _inspect_pr_state(**updates: object) -> dict[str, object]:
    repository = str(updates.pop("repository", runner.REPO))
    pr: dict[str, object] = {
        "number": 123,
        "state": "open",
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "base": {
            "ref": "main",
            "sha": "b" * 40,
            "repo": {"full_name": repository},
        },
        "head": {"ref": "runner/issue-123", "sha": HEAD_SHA},
    }
    pr.update(updates.pop("pr", {}))
    state: dict[str, object] = {
        "pr": pr,
        "files": [{"filename": "docs/example.md"}],
        "compare": {"status": "ahead", "ahead_by": 1, "behind_by": 0},
        "combined_status": {
            "state": "success",
            "statuses": [{"context": "pytest", "state": "success"}],
        },
        "check_runs": [],
    }
    state.update(updates)
    return state


def _validation_receipt_issue_json(
    *,
    repository: str = runner.REPO,
    pr_number: int = 123,
    head_sha: str = HEAD_SHA,
    base_sha: str = "b" * 40,
    status: str = "DONE",
    receipt_head_sha: str | None = None,
    receipt_base_sha: str | None = None,
) -> str:
    idempotency_key = runner._continuation_issue_idempotency_key(
        repository, pr_number, head_sha, base_sha
    )
    receipt = "\n".join(
        (
            f"{status}: Runner host maintenance task completed.",
            f"maintenance_task_id={runner.VALIDATE_PR_BRANCH}",
            f"repository={repository}",
            f"pull_request={pr_number}",
            f"validation_checkout_head_sha={receipt_head_sha or head_sha}",
            f"validation_base_sha={receipt_base_sha or base_sha}",
            "validation_final_status=clean",
            "success_criteria=met" if status == "DONE" else "success_criteria=not_met",
        )
    )
    return json.dumps(
        [
            {
                "number": 3001,
                "body": f"idempotency_key: {idempotency_key}",
                "labels": [{"name": runner.LABEL_AGENT_TASK}],
                "comments": [{"body": receipt}],
            }
        ]
    )


def _validation_receipt_issue_view_json(**kwargs: object) -> str:
    issue = json.loads(_validation_receipt_issue_json(**kwargs))[0]
    return json.dumps(
        {
            "body": issue["body"],
            "labels": issue["labels"],
            "comments": issue["comments"],
        }
    )


def _telegram_response() -> mock.MagicMock:
    response = mock.MagicMock()
    response.__enter__.return_value = response
    return response


def _json_response(payload: object) -> mock.MagicMock:
    response = _telegram_response()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def _request_payload(urlopen: mock.MagicMock) -> dict[str, list[str]]:
    request = urlopen.call_args.args[0]
    return urllib.parse.parse_qs(request.data.decode("utf-8"))


def test_home_edge_debian_media_bootstrap_is_allowlisted_and_dispatches_exact_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_bootstrap = pytest.importorskip(
        "skeleton_media.home_edge.debian_media_bootstrap"
    )
    captured: dict[str, object] = {}

    def fake_read_sha(ref: str) -> str:
        assert ref in {"main", "origin/main"}
        return HEAD_SHA

    def fake_execute(body: str, *, registered_clean_main_sha: str, github_main_sha: str):
        captured["body"] = body
        captured["registered_clean_main_sha"] = registered_clean_main_sha
        captured["github_main_sha"] = github_main_sha
        return _media_bootstrap_receipt()

    monkeypatch.setattr(runner, "_read_exact_git_sha", fake_read_sha)
    monkeypatch.setattr(
        media_bootstrap,
        "execute_debian_media_bootstrap_task",
        fake_execute,
    )

    report = runner.dispatch_runtime_maintenance_task(
        runner.HOME_EDGE_01_DEBIAN_MEDIA_BOOTSTRAP_V1,
        str(runner.ROOT),
        _media_bootstrap_issue_body(),
    )

    assert runner.HOME_EDGE_01_DEBIAN_MEDIA_BOOTSTRAP_V1 in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert report.startswith("DONE:")
    assert "maintenance_task_id=home_edge_01_debian_media_bootstrap_v1" in report
    assert "packages_required_count=20" in report
    assert "mutation_executor_receipt_hash=" + "b" * 64 in report
    assert "final_postcheck_receipt_hash=" + "c" * 64 in report
    assert "physical_audio_status=physical_pending" in report
    assert "success_criteria=met" in report
    assert captured["registered_clean_main_sha"] == HEAD_SHA
    assert captured["github_main_sha"] == HEAD_SHA
    assert "/home/" not in report
    assert "SECRET" not in report


def test_home_edge_debian_media_bootstrap_malformed_input_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("skeleton_media.home_edge.debian_media_bootstrap")
    monkeypatch.setattr(runner, "_read_exact_git_sha", lambda _ref: HEAD_SHA)

    report = runner.dispatch_runtime_maintenance_task(
        runner.HOME_EDGE_01_DEBIAN_MEDIA_BOOTSTRAP_V1,
        str(runner.ROOT),
        _media_bootstrap_issue_body() + "\nPackage: evil",
    )

    assert report.startswith("BLOCKED:")
    assert "reason=unknown_runtime_input_field" in report


def test_home_edge_post_migration_reconcile_is_allowlisted_and_dispatches_sanitized_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_read_sha(ref: str) -> str:
        assert ref in {"main", "origin/main"}
        return HEAD_SHA

    def fake_execute(body: str, *, registered_clean_main_sha: str, github_main_sha: str):
        captured["body"] = body
        captured["registered_clean_main_sha"] = registered_clean_main_sha
        captured["github_main_sha"] = github_main_sha
        return _post_migration_receipt()

    monkeypatch.setattr(runner, "_read_exact_git_sha", fake_read_sha)
    monkeypatch.setattr(
        post_migration_reconcile,
        "execute_post_migration_reconcile_task",
        fake_execute,
    )

    report = runner.dispatch_runtime_maintenance_task(
        runner.HOME_EDGE_01_POST_MIGRATION_RECONCILE_V1,
        str(runner.ROOT),
        _post_migration_issue_body(),
    )

    assert (
        runner.HOME_EDGE_01_POST_MIGRATION_RECONCILE_V1
        in runner.RUNTIME_MAINTENANCE_TASK_IDS
    )
    assert report.startswith("DONE:")
    assert "maintenance_task_id=home_edge_01_post_migration_reconcile_v1" in report
    assert "gallery_status=repaired_healthy" in report
    assert "watchdog_critical_count=0" in report
    assert "watchdog_warning_count=0" in report
    assert "canonical_memory_post_step=home_edge_audit_persist_v1" in report
    assert "mutation_executor_receipt_hash=" + "b" * 64 in report
    assert captured["registered_clean_main_sha"] == HEAD_SHA
    assert captured["github_main_sha"] == HEAD_SHA
    assert "/home/" not in report
    assert "SECRET" not in report


def test_home_edge_post_migration_reconcile_malformed_input_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_read_exact_git_sha", lambda _ref: HEAD_SHA)

    report = runner.dispatch_runtime_maintenance_task(
        runner.HOME_EDGE_01_POST_MIGRATION_RECONCILE_V1,
        str(runner.ROOT),
        _post_migration_issue_body() + "\nCommand: evil",
    )

    assert report.startswith("BLOCKED:")
    assert "reason=unknown_runtime_input_field" in report


def test_home_edge_media_source_snapshot_is_allowlisted_and_dispatches_sanitized_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_source_snapshot = pytest.importorskip(
        "skeleton_media.home_edge.media_source_snapshot"
    )
    captured: dict[str, object] = {}

    def fake_read_sha(ref: str) -> str:
        assert ref in {"main", "origin/main"}
        return HEAD_SHA

    def fake_execute(body: str, *, registered_clean_main_sha: str, github_main_sha: str):
        captured["body"] = body
        captured["registered_clean_main_sha"] = registered_clean_main_sha
        captured["github_main_sha"] = github_main_sha
        return _media_source_snapshot_receipt()

    monkeypatch.setattr(runner, "_read_exact_git_sha", fake_read_sha)
    monkeypatch.setattr(
        media_source_snapshot,
        "execute_media_source_snapshot_task",
        fake_execute,
    )

    report = runner.dispatch_runtime_maintenance_task(
        runner.HOME_EDGE_01_MEDIA_SOURCE_SNAPSHOT_V1,
        str(runner.ROOT),
        _media_source_snapshot_issue_body(),
    )

    assert runner.HOME_EDGE_01_MEDIA_SOURCE_SNAPSHOT_V1 in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert report.startswith("DONE:")
    assert "maintenance_task_id=home_edge_01_media_source_snapshot_v1" in report
    assert "source_identity=verified" in report
    assert "source_version_marker=v63" in report
    assert "source_bytes=1234" in report
    assert "source_sha256=" + "c" * 64 in report
    assert "python_parse_status=ok" in report
    assert "video_route_present=True" in report
    assert "health_route_present=True" in report
    assert "private_artifact_written=True" in report
    assert "private_artifact_hash_matches=True" in report
    assert "executor_receipt_hash=" + "b" * 64 in report
    assert "success_criteria=met" in report
    assert captured["registered_clean_main_sha"] == HEAD_SHA
    assert captured["github_main_sha"] == HEAD_SHA
    assert "/opt/skeleton/cast/app.py" not in report
    assert "private_source_b64" not in report
    assert "10.44.55.66" not in report


def test_home_edge_media_source_snapshot_registry_exposes_only_exact_fixed_task_id() -> None:
    variants = {
        task_id
        for task_id in runner.RUNTIME_MAINTENANCE_TASK_IDS
        if "media_source_snapshot" in task_id
    }

    assert variants == {runner.HOME_EDGE_01_MEDIA_SOURCE_SNAPSHOT_V1}
    assert (
        runner.dispatch_runtime_maintenance_task(
            "home_edge_01_media_source_snapshot_v1_extra",
            str(runner.ROOT),
            _media_source_snapshot_issue_body(),
        ).startswith("BLOCKED:")
    )


def _codex_health_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pre_sha: str = HEAD_SHA,
    post_sha: str = HEAD_SHA,
    pre_clean: bool = True,
    post_clean: bool = True,
    executable: str | None = "/registered/codex",
    auth_home: Path | None = Path("/canonical/.codex"),
    selected_model: str | None = "gpt-5.5",
):
    checkout = mock.Mock(
        checkout_path=Path("/canonical/skeleton"), status_lines=[]
    )
    state = {"clean": 0, "sha": 0}
    monkeypatch.setattr(
        runner, "_registered_skeleton_checkout", lambda _task_id: (checkout, None)
    )
    monkeypatch.setattr(
        runner,
        "_verify_skeleton_checkout_present",
        lambda _task_id, _checkout: None,
    )
    monkeypatch.setattr(
        runner,
        "_read_skeleton_origin",
        lambda _task_id, _checkout, _lines: None,
    )
    monkeypatch.setattr(
        runner,
        "_read_skeleton_current_branch",
        lambda _task_id, _path, _lines: None,
    )

    def read_clean(_task_id, _path, _lines):
        index = min(state["clean"], 1)
        state["clean"] += 1
        return None if (pre_clean, post_clean)[index] else "BLOCKED"

    def read_sha(_task_id, _path, _ref, _lines, _step):
        index = min(state["sha"], 1)
        state["sha"] += 1
        return (pre_sha, post_sha)[index], None

    monkeypatch.setattr(runner, "_read_skeleton_clean_state", read_clean)
    monkeypatch.setattr(runner, "_read_skeleton_sha", read_sha)
    executable_probe = mock.Mock(return_value=executable)
    auth_probe = mock.Mock(return_value=auth_home)
    sanitizer = mock.Mock(return_value={"HOME": "/canonical", "PATH": "/usr/bin"})
    monkeypatch.setattr(
        runner, "_runner_codex_primary_executable", executable_probe
    )
    monkeypatch.setattr(runner, "_runner_codex_primary_auth_home", auth_probe)
    monkeypatch.setattr(runner, "sanitize_codegen_child_environment", sanitizer)
    monkeypatch.setattr(runner, "selected_codex_model", lambda: selected_model)
    return checkout, state, executable_probe, auth_probe, sanitizer


def _codex_health_run(
    *,
    provider_code: int = 0,
    provider_output: str = "CODEX_HEALTH_OK",
    help_output: str = (
        "--sandbox <MODE> [read-only|workspace-write] --ephemeral "
        "--ignore-user-config --ignore-rules"
    ),
    seen_envs: list[dict[str, str]] | None = None,
):
    def run(args, cwd=None, **kwargs):
        if seen_envs is not None:
            seen_envs.append(dict(runner._RUN_COMMAND_ENV_OVERRIDE.get() or {}))
        if args == ["/registered/codex", "exec", "--help"]:
            return 0, help_output
        return provider_code, provider_output

    return run


def _codex_health_receipt_keys(report: str) -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in report.splitlines()
        if line.count("=") == 1
        and not line.startswith(("maintenance_task_id=", "success_criteria="))
    }


def test_codex_primary_health_exact_registration_command_and_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, state, _executable, _auth, sanitizer = _codex_health_fakes(
        monkeypatch
    )
    seen_envs: list[dict[str, str]] = []
    run = mock.Mock(side_effect=_codex_health_run(seen_envs=seen_envs))
    monkeypatch.setattr(runner, "run_command", run)

    report = runner.dispatch_runtime_maintenance_task(
        runner.RUNNER_CODEX_PRIMARY_HEALTH_PROBE,
        str(runner.ROOT),
        _codex_primary_health_issue_body(),
    )

    assert runner.RUNNER_CODEX_PRIMARY_HEALTH_PROBE in runner.RUNTIME_MAINTENANCE_TASK_IDS
    assert runner.RUNNER_CODEX_PRIMARY_HEALTH_PROBE in runner.PROTECTED_MAINTENANCE_TASK_IDS
    assert runner.maintenance_report_status(report) == "DONE"
    assert "maintenance_task_id=runner_codex_primary_health_probe" in report
    assert "verdict=CODEX_HEALTHY" in report
    assert "fallback_invoked=false" in report
    assert "mutation_performed=false" in report
    assert "_v1" not in report
    assert state == {"clean": 2, "sha": 2}
    sanitizer.assert_called_once()
    assert sanitizer.call_args.kwargs == {"authority_environment": {}}
    assert len(run.call_args_list) == 2
    help_call, provider_call = run.call_args_list
    assert help_call.args[0] == ["/registered/codex", "exec", "--help"]
    assert provider_call.args[0] == [
        "/registered/codex",
        "exec",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--color",
        "never",
        "--model",
        "gpt-5.5",
        "--cd",
        str(checkout.checkout_path),
        "-",
    ]
    assert provider_call.kwargs["input"] == (
        "Return exactly CODEX_HEALTH_OK. Do not inspect, create, modify, or "
        "delete files. Do not run tools."
    )
    assert provider_call.kwargs["cwd"] == checkout.checkout_path
    assert all(env["CODEX_HOME"] == "/canonical/.codex" for env in seen_envs)
    assert runner._RUN_COMMAND_ENV_OVERRIDE.get() is None
    assert _codex_health_receipt_keys(report) == {
        "schema",
        "expected_current_sha_equal",
        "checkout_clean",
        "executable_available",
        "auth_binding_present",
        "auth_mode",
        "selected_model",
        "verdict",
        "retry_at",
        "fallback_invoked",
        "mutation_performed",
    }


@pytest.mark.parametrize(
    "body",
    (
        "",
        "Mode: RUNTIME_MAINTENANCE_TASK",
        _codex_primary_health_issue_body() + "\nProvider: openhands",
        _codex_primary_health_issue_body() + "\nRepository: alanua/Skeleton",
        _codex_primary_health_issue_body(expected_sha="not-a-sha"),
        _codex_primary_health_issue_body(expected_sha=HEAD_SHA.upper()),
    ),
)
def test_codex_primary_health_malformed_metadata_makes_zero_calls(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    registered = mock.Mock()
    run = mock.Mock()
    monkeypatch.setattr(runner, "_registered_skeleton_checkout", registered)
    monkeypatch.setattr(runner, "run_command", run)

    report = runner.runner_codex_primary_health_probe(body)

    assert "verdict=CODEX_ROUTE_UNAVAILABLE" in report
    registered.assert_not_called()
    run.assert_not_called()


def test_codex_primary_health_approval_framing_is_not_probe_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _codex_health_fakes(monkeypatch)
    run = mock.Mock(side_effect=_codex_health_run())
    monkeypatch.setattr(runner, "run_command", run)
    report = runner.runner_codex_primary_health_probe(
        _codex_primary_health_issue_body()
        + "\nApproval Reference: public-safe-framing"
    )
    ambiguous = runner.runner_codex_primary_health_probe(
        _codex_primary_health_issue_body()
        + "\nApproval Reference: public-safe-framing"
        + "\nOperator Approval: public-safe-framing"
    )

    assert "verdict=CODEX_HEALTHY" in report
    assert "verdict=CODEX_ROUTE_UNAVAILABLE" in ambiguous
    assert len(run.call_args_list) == 2


@pytest.mark.parametrize(
    ("pre_sha", "pre_clean", "field"),
    (("b" * 40, True, "expected_current_sha_equal=false"),
     (HEAD_SHA, False, "checkout_clean=false")),
)
def test_codex_primary_health_preflight_blocks_before_executable(
    monkeypatch: pytest.MonkeyPatch,
    pre_sha: str,
    pre_clean: bool,
    field: str,
) -> None:
    _checkout, _state, executable, _auth, _sanitizer = _codex_health_fakes(
        monkeypatch, pre_sha=pre_sha, pre_clean=pre_clean
    )
    run = mock.Mock()
    monkeypatch.setattr(runner, "run_command", run)

    report = runner.runner_codex_primary_health_probe(
        _codex_primary_health_issue_body()
    )

    assert field in report
    assert "verdict=CODEX_ROUTE_UNAVAILABLE" in report
    executable.assert_not_called()
    run.assert_not_called()


@pytest.mark.parametrize(
    ("executable", "auth_home", "verdict"),
    ((None, Path("/canonical/.codex"), "CODEX_EXECUTABLE_UNAVAILABLE"),
     ("/registered/codex", None, "CODEX_AUTH_FAILURE")),
)
def test_codex_primary_health_missing_binding_makes_zero_cli_calls(
    monkeypatch: pytest.MonkeyPatch,
    executable: str | None,
    auth_home: Path | None,
    verdict: str,
) -> None:
    _codex_health_fakes(
        monkeypatch, executable=executable, auth_home=auth_home
    )
    run = mock.Mock()
    monkeypatch.setattr(runner, "run_command", run)

    report = runner.runner_codex_primary_health_probe(
        _codex_primary_health_issue_body()
    )

    assert f"verdict={verdict}" in report
    run.assert_not_called()


def test_codex_primary_health_auth_metadata_rejects_insecure_binding(
    tmp_path: Path,
) -> None:
    home = tmp_path / "runner-home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True, mode=0o700)
    auth_file = codex_home / "auth.json"
    auth_file.write_text("not-read-by-probe", encoding="utf-8")
    codex_home.chmod(0o700)
    auth_file.chmod(0o600)
    assert runner._runner_codex_primary_auth_home({"HOME": str(home)}) == codex_home

    auth_file.chmod(0o644)
    assert runner._runner_codex_primary_auth_home({"HOME": str(home)}) is None
    auth_file.unlink()
    auth_file.symlink_to(tmp_path / "missing-auth")
    assert runner._runner_codex_primary_auth_home({"HOME": str(home)}) is None


def test_codex_primary_health_executable_requires_existing_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    marker = mock.Mock(return_value=False)
    pinned = mock.Mock(return_value=str(executable))
    monkeypatch.setattr(runner, "pinned_codex_recovery_marker_present", marker)
    monkeypatch.setattr(runner, "pinned_codex_runtime_path", pinned)
    assert runner._runner_codex_primary_executable({}) is None
    pinned.assert_not_called()

    marker.return_value = True
    assert runner._runner_codex_primary_executable({}) == str(executable)

