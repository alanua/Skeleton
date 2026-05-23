from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path
from unittest import mock

import pytest

from scripts import runner_poll_github_tasks as runner
from scripts import telegram_callback_poller as callback_poller


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


def _plain_done_message(issue_number: int = 129) -> str:
    return runner.build_telegram_message(issue_number, "DONE", DONE_REPORT)


def _write_project_registry(
    path: Path,
    *,
    projects: dict[str, dict[str, object]],
    default_project: str = "skeleton",
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "skeleton.project_registry.v1",
                "default_project": default_project,
                "projects": projects,
            }
        ),
        encoding="utf-8",
    )


def _registry_entry(
    tmp_path: Path,
    project_id: str,
    repository: str,
    *,
    enabled: bool = True,
) -> dict[str, object]:
    return {
        "project_id": project_id,
        "repository": repository,
        "checkout_path": str(tmp_path / "checkouts" / project_id),
        "worktree_root": str(tmp_path / "worktrees" / project_id),
        "base_branch": "main",
        "runner_modes": ["codex_issue_worktree" if project_id == "skeleton" else "planning_only"],
        "enabled": enabled,
    }


def test_blocked_output_classifier_detects_runner_blockers() -> None:
    cases = {
        "BLOCKED": "BLOCKED",
        "Blocked: waiting for access": "BLOCKED",
        "missing capability: docker": "missing capability",
        "wrong worktree selected": "wrong worktree",
        "not target repo": "not target repo",
        "writer unavailable": "writer unavailable",
        "cancelled by operator": "cancelled",
        "no build files were present": "no build files",
        "PlatformIO not available": "PlatformIO not available",
        "no firmware target exists": "no firmware",
        "assigned worktree is not target": "assigned worktree is not target",
    }

    for output, expected_marker in cases.items():
        assert runner.blocked_output_marker(output) == expected_marker



def test_blocked_output_classifier_ignores_echoed_transcript_markers() -> None:
    output = """Changed files:
- scripts/runner_poll_github_tasks.py
- tests/test_runner_poll_github_tasks.py

Test count: 416 passed, 3 skipped
Reading additional input from stdin...
OpenAI Codex v0.125.0
--------
user
Goal text mentions BLOCKED and runner:blocked in echoed instructions.
exec
LABEL_BLOCKED = "runner:blocked"
"""

    assert runner.final_codex_answer(output).startswith("Changed files:")
    assert runner.blocked_output_marker(output) is None


def test_blocked_output_classifier_keeps_real_final_marker_detection() -> None:
    output = """BLOCKED: missing capability

Reading additional input from stdin...
OpenAI Codex v0.125.0
"""

    assert runner.blocked_output_marker(output) == "BLOCKED"


def test_runner_report_status_blocks_file_change_done_without_draft_pr() -> None:
    report = DONE_REPORT.replace(f"\nDraft PR: {PR_URL}", "")

    assert runner.runner_report_status(report) == "BLOCKED"


def test_runner_report_status_allows_no_change_done_without_draft_pr() -> None:
    report = "DONE: Codex completed successfully with no file changes."

    assert runner.runner_report_status(report) == "DONE"


def test_worktree_path_uses_env_root_when_set(tmp_path: Path) -> None:
    configured_root = tmp_path / "runner-worktrees"
    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(configured_root)}, clear=True
    ):
        assert runner.issue_worktree_path(139) == configured_root / "issue-139"


def test_worktree_path_falls_back_to_default_root() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        assert runner.worktree_root() == runner.DEFAULT_WORKTREE_ROOT
        assert runner.issue_worktree_path(139) == runner.DEFAULT_WORKTREE_ROOT / "issue-139"


def test_worktree_path_includes_issue_number(tmp_path: Path) -> None:
    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ):
        assert runner.issue_worktree_path(912).name == "issue-912"


def test_target_repository_worktree_paths_are_deterministic(tmp_path: Path) -> None:
    skeleton_root = tmp_path / "skeleton"
    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(skeleton_root)}, clear=True
    ):
        bauclock_path = runner.target_repository_issue_worktree_path(
            "alanua/bauclock", 912
        )

        assert runner.target_repository_worktree_root("alanua/Skeleton") == skeleton_root
        assert runner.target_repository_worktree_root("alanua/bauclock") == (
            tmp_path / "bauclock"
        )
        assert runner.target_repository_worktree_root("alanua/Lavalamp") == (
            tmp_path / "lavalamp"
        )
        assert bauclock_path == tmp_path / "bauclock" / "issue-912"
        assert bauclock_path == runner.target_repository_issue_worktree_path(
            "alanua/bauclock", 912
        )


def test_unknown_target_repository_worktree_root_is_rejected() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        runner.target_repository_worktree_root("alanua/unknown")


def test_target_repository_worktree_path_rejects_other_repository_root(
    tmp_path: Path,
) -> None:
    skeleton_root = tmp_path / "skeleton"
    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(skeleton_root)}, clear=True
    ), pytest.raises(ValueError, match="outside configured root"):
        runner.ensure_safe_target_repository_worktree_path(
            "alanua/bauclock", skeleton_root / "issue-912"
        )


def test_unsafe_worktree_paths_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runner-worktrees"
    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(root)}, clear=True
    ):
        for unsafe_path in (root, tmp_path / "issue-139"):
            try:
                runner.ensure_safe_worktree_path(unsafe_path)
            except ValueError:
                continue
            raise AssertionError(f"unsafe worktree path was accepted: {unsafe_path}")


def test_prepare_issue_worktree_adds_runner_issue_branch(tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    coordinator = tmp_path / "coordinator"
    coordinator.mkdir()

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(worktree_root)}, clear=True
    ), mock.patch.object(
        runner, "run_command", side_effect=((0, "fetched"), (0, "added"))
    ) as run_command:
        code, output, path = runner.prepare_issue_worktree(139, coordinator)

    assert code == 0
    assert "git worktree add" in output
    assert path == (worktree_root / "issue-139").resolve()
    assert run_command.call_args_list == [
        mock.call(["git", "fetch", "origin"], cwd=coordinator),
        mock.call(
            [
                "git",
                "worktree",
                "add",
                "-B",
                "runner/issue-139",
                str(path),
                "origin/main",
            ],
            cwd=coordinator,
        ),
    ]


def test_stale_dirty_worktree_blocks_instead_of_deleting(tmp_path: Path) -> None:
    worktree_path = tmp_path / "worktrees" / "issue-139"
    worktree_path.mkdir(parents=True)

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path / "worktrees")}, clear=True
    ), mock.patch.object(
        runner, "run_command", return_value=(0, " M scripts/runner_poll_github_tasks.py")
    ) as run_command:
        code, output, path = runner.prepare_issue_worktree(139, tmp_path / "coordinator")

    assert code != 0
    assert path == worktree_path.resolve()
    assert "dirty" in output
    assert "cleanup" in output
    run_command.assert_called_once_with(["git", "status", "--short"], cwd=path)


def test_cleanup_issue_worktree_refuses_path_outside_configured_root(
    tmp_path: Path,
) -> None:
    configured_root = tmp_path / "worktrees"
    outside_path = tmp_path / "issue-139"

    with mock.patch.object(
        runner, "issue_worktree_path", return_value=outside_path
    ), mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(configured_root)}, clear=True
    ), mock.patch.object(runner, "run_command") as run_command:
        code, output = runner.cleanup_issue_worktree(139, tmp_path / "coordinator")

    assert code != 0
    assert "outside configured root" in output
    run_command.assert_not_called()


def test_process_issue_runs_codex_in_prepared_issue_worktree(tmp_path: Path) -> None:
    coordinator = tmp_path / "coordinator"
    issue_path = tmp_path / "worktrees" / "issue-139"
    issue = {"number": 139, "title": "Worktree stage", "body": "```task\nDo it\n```"}

    with mock.patch.object(runner, "set_issue_label"), mock.patch.object(
        runner, "prepare_issue_worktree", return_value=(0, "ready", issue_path)
    ) as prepare, mock.patch.object(
        runner, "ensure_clean_worktree", side_effect=AssertionError("coordinator checked")
    ), mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_success", return_value="DONE report"
    ) as finalize, mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "cleanup_issue_worktree"
    ):
        runner.process_issue(issue, workdir=str(coordinator))

    prepare.assert_called_once_with(139, str(coordinator))
    run_codex.assert_called_once_with("Do it", str(issue_path))
    finalize.assert_called_once_with(issue, str(issue_path), "codex output")


def test_poll_once_processes_issues_single_lane() -> None:
    issues = [{"number": 139}, {"number": 140}]
    with mock.patch.object(
        runner, "get_ready_issues", return_value=issues
    ), mock.patch.object(runner, "process_issue") as process_issue:
        count = runner.poll_once(workdir="/coordinator")

    assert count == 2
    assert process_issue.call_args_list == [
        mock.call(issues[0], workdir="/coordinator"),
        mock.call(issues[1], workdir="/coordinator"),
    ]


def test_runner_task_defaults_to_default_lane() -> None:
    task, reason = runner.extract_runner_task("```task\nDo it\n```")

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        lane=runner.DEFAULT_RUNNER_LANE,
    )


def test_runner_task_accepts_allowlisted_lane_name() -> None:
    task, reason = runner.extract_runner_task("Runner Lane: lane-1\n\n```task\nDo it\n```")

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        lane=runner.RunnerLane("lane-1"),
        has_lane_metadata=True,
    )


def test_runner_task_accepts_allowlisted_target_repository() -> None:
    task, reason = runner.extract_runner_task(
        "Target Repository: alanua/bauclock\n\n```task\nDo it\n```"
    )

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        target_project="bauclock",
        target_repository="alanua/bauclock",
        has_target_repository_metadata=True,
    )
    assert runner.ALLOWED_TARGET_REPOSITORIES == frozenset(
        ("alanua/Skeleton", "alanua/bauclock", "alanua/Lavalamp")
    )


def test_runner_task_ignores_lane_text_inside_task_fence() -> None:
    task, reason = runner.extract_runner_task("```task\nLane: deploy\nKeep it as prose.\n```")

    assert reason is None
    assert task == runner.RunnerTask(
        content="Lane: deploy\nKeep it as prose.",
        lane=runner.DEFAULT_RUNNER_LANE,
    )


def test_process_issue_blocks_non_allowlisted_runner_lane_before_claim() -> None:
    issue = {
        "number": 141,
        "title": "Lane metadata",
        "body": "Runner Lane: deploy\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "Runner lane `deploy` is not allowlisted" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_non_allowlisted_target_repository_before_claim() -> None:
    issue = {
        "number": 142,
        "title": "Target repository metadata",
        "body": "Target Repository: alanua/unknown\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "Target repository `alanua/unknown` is not allowlisted" in (
        block.call_args.args[1]
    )
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_disabled_target_project_before_claim(tmp_path: Path) -> None:
    registry_path = tmp_path / "PROJECT_REGISTRY.yaml"
    checkout = tmp_path / "checkouts" / "disabled"
    checkout.mkdir(parents=True)
    _write_project_registry(
        registry_path,
        default_project="skeleton",
        projects={
            "skeleton": _registry_entry(tmp_path, "skeleton", "alanua/Skeleton"),
            "disabled": _registry_entry(
                tmp_path, "disabled", "alanua/disabled", enabled=False
            ),
        },
    )
    issue = {
        "number": 144,
        "title": "Disabled target",
        "body": "Target Project: disabled\n\n```task\nDo it\n```",
    }

    with mock.patch.dict(
        os.environ, {"SKELETON_PROJECT_REGISTRY": str(registry_path)}, clear=True
    ), mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "disabled" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_missing_checkout_before_codex(tmp_path: Path) -> None:
    registry_path = tmp_path / "PROJECT_REGISTRY.yaml"
    _write_project_registry(
        registry_path,
        default_project="skeleton",
        projects={
            "skeleton": _registry_entry(tmp_path, "skeleton", "alanua/Skeleton"),
            "future": _registry_entry(tmp_path, "future", "alanua/future"),
        },
    )
    issue = {
        "number": 145,
        "title": "Missing checkout",
        "body": "Target Project: future\n\n```task\nDo it\n```",
    }

    with mock.patch.dict(
        os.environ, {"SKELETON_PROJECT_REGISTRY": str(registry_path)}, clear=True
    ), mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "checkout_path does not exist" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_wrong_remote_before_codex(tmp_path: Path) -> None:
    registry_path = tmp_path / "PROJECT_REGISTRY.yaml"
    checkout = tmp_path / "checkouts" / "future"
    checkout.mkdir(parents=True)
    _write_project_registry(
        registry_path,
        default_project="skeleton",
        projects={
            "skeleton": _registry_entry(tmp_path, "skeleton", "alanua/Skeleton"),
            "future": _registry_entry(tmp_path, "future", "alanua/future"),
        },
    )
    issue = {
        "number": 146,
        "title": "Wrong remote",
        "body": "Target Project: future\n\n```task\nDo it\n```",
    }

    with mock.patch.dict(
        os.environ, {"SKELETON_PROJECT_REGISTRY": str(registry_path)}, clear=True
    ), mock.patch.object(
        runner, "registry_remote_reader", return_value="alanua/wrong"
    ), mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "checkout remote does not match" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_runner_task_accepts_future_target_from_registry(tmp_path: Path) -> None:
    registry_path = tmp_path / "PROJECT_REGISTRY.yaml"
    _write_project_registry(
        registry_path,
        default_project="skeleton",
        projects={
            "skeleton": _registry_entry(tmp_path, "skeleton", "alanua/Skeleton"),
            "future": _registry_entry(tmp_path, "future", "alanua/future"),
        },
    )

    with mock.patch.dict(
        os.environ, {"SKELETON_PROJECT_REGISTRY": str(registry_path)}, clear=True
    ):
        task, reason = runner.extract_runner_task(
            "Target Repository: alanua/future\n\n```task\nDo it\n```"
        )

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        target_project="future",
        target_repository="alanua/future",
        has_target_repository_metadata=True,
    )


def test_process_issue_does_not_execute_allowlisted_cross_repo_target_yet() -> None:
    issue = {
        "number": 143,
        "title": "Target repository stage 1",
        "body": "Target Repository: alanua/Lavalamp\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "validate_runner_target_ready"
    ), mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "planning-only" in block.call_args.args[1]
    set_label.assert_not_called()
    prepare_branch.assert_not_called()
    run_codex.assert_not_called()


def test_extracts_bounded_telegram_approved_merge_request() -> None:
    mode, request, reason = runner.extract_telegram_approved_pr_merge_request(
        _merge_issue_body()
    )

    assert mode is True
    assert reason is None
    assert request == runner.TelegramApprovedPrMergeRequest(
        pr_number=123,
        approved_head_sha=HEAD_SHA,
        callback_digest=CALLBACK_DIGEST,
    )


def test_callback_merge_request_smoke_uses_signed_callback_approval_record() -> None:
    callback_data = f"tpr1:approve:p123:{HEAD_SHA[:8]}:{CALLBACK_DIGEST}"
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            runner.TELEGRAM_CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        callback_poller.urllib.request,
        "urlopen",
        side_effect=(
            _json_response({"number": 123, "head": {"sha": HEAD_SHA}}),
            _json_response({"id": 88}),
            _json_response({"number": 910}),
        ),
    ) as urlopen:
        callback_result = callback_poller.handle_callback_query(
            {"id": "callback-query-1", "data": callback_data}
        )

    merge_issue_request = urlopen.call_args_list[-1].args[0]
    body = json.loads(merge_issue_request.data.decode("utf-8"))["body"]

    mode, request, reason = runner.extract_telegram_approved_pr_merge_request(body)

    assert mode is True
    assert reason is None
    assert request == runner.TelegramApprovedPrMergeRequest(
        pr_number=123,
        approved_head_sha=HEAD_SHA,
        callback_digest=CALLBACK_DIGEST,
    )

    with mock.patch.dict(
        os.environ, {runner.TELEGRAM_CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET}, clear=True
    ), mock.patch.object(
        runner,
        "get_pr_merge_state",
        return_value=_merge_pr_state(
            comments=[{"body": str(callback_result["comment"])}]
        ),
    ), mock.patch.object(runner, "run_command", return_value=(0, "merged")) as run:
        report = runner.execute_telegram_approved_pr_merge(request)

    assert callback_result["runner_merge_request"] == "requested"
    assert report.startswith("DONE:")
    run.assert_called_once()


@pytest.mark.parametrize(
    ("body", "reason"),
    (
        (_merge_issue_body(action="merge"), "action must be squash"),
        (
            _merge_issue_body(approval_source="issue_body"),
            "source is not allowlisted",
        ),
        (_merge_issue_body(head_sha="not-a-sha"), "head SHA is malformed"),
    ),
)
def test_blocks_malformed_telegram_approved_merge_issue(
    body: str, reason: str
) -> None:
    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue({"number": 141, "title": "Merge", "body": body})

    assert reason in block.call_args.args[1]
    run_codex.assert_not_called()


def test_telegram_approved_merge_issue_bypasses_codex_and_posts_merge_report() -> None:
    issue = {"number": 141, "title": "Merge", "body": _merge_issue_body()}
    with mock.patch.object(runner, "set_issue_label") as set_label, mock.patch.object(
        runner, "process_telegram_approved_pr_merge_issue"
    ) as process_merge, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert set_label.call_args_list == [
        mock.call(141, runner.LABEL_READY, runner.LABEL_RUNNING)
    ]
    process_merge.assert_called_once_with(
        141,
        runner.TelegramApprovedPrMergeRequest(
            pr_number=123,
            approved_head_sha=HEAD_SHA,
            callback_digest=CALLBACK_DIGEST,
        ),
    )
    run_codex.assert_not_called()


def test_executes_only_head_matched_squash_merge_for_approved_pr() -> None:
    request = runner.TelegramApprovedPrMergeRequest(123, HEAD_SHA, CALLBACK_DIGEST)
    with mock.patch.dict(
        os.environ, {runner.TELEGRAM_CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET}, clear=True
    ), mock.patch.object(
        runner, "get_pr_merge_state", return_value=_merge_pr_state()
    ), mock.patch.object(runner, "run_command", return_value=(0, "merged")) as run:
        report = runner.execute_telegram_approved_pr_merge(request)

    assert report.startswith("DONE:")
    run.assert_called_once_with(
        [
            "gh",
            "pr",
            "merge",
            "123",
            "--repo",
            runner.REPO,
            "--squash",
            "--match-head-commit",
            HEAD_SHA,
        ]
    )


def test_blocks_telegram_approved_merge_when_callback_digest_is_not_signed() -> None:
    request = runner.TelegramApprovedPrMergeRequest(123, HEAD_SHA, "0123456789ab")
    with mock.patch.dict(
        os.environ, {runner.TELEGRAM_CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET}, clear=True
    ), mock.patch.object(
        runner, "get_pr_merge_state", return_value=_merge_pr_state()
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.execute_telegram_approved_pr_merge(request)

    assert report == "BLOCKED: Telegram approve callback HMAC signature is invalid."
    run.assert_not_called()


@pytest.mark.parametrize(
    ("updates", "reason"),
    (
        ({"state": "CLOSED"}, "not open"),
        ({"isDraft": True}, "still draft"),
        ({"mergeable": "CONFLICTING"}, "not mergeable"),
        ({"headRefOid": "b" * 40}, "head does not match"),
        ({"comments": []}, "Signed Telegram approve audit"),
        (
            {
                "comments": [
                    {
                        "body": _merge_comments()[0]["body"].replace(
                            f"Verified head SHA: {HEAD_SHA}",
                            f"Verified head SHA: {'b' * 40}",
                        )
                    }
                ]
            },
            "Signed Telegram approve audit",
        ),
    ),
)
def test_blocks_telegram_approved_merge_when_verification_fails(
    updates: dict[str, object], reason: str
) -> None:
    request = runner.TelegramApprovedPrMergeRequest(123, HEAD_SHA, CALLBACK_DIGEST)
    with mock.patch.dict(
        os.environ, {runner.TELEGRAM_CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET}, clear=True
    ), mock.patch.object(
        runner, "get_pr_merge_state", return_value=_merge_pr_state(**updates)
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.execute_telegram_approved_pr_merge(request)

    assert report.startswith("BLOCKED:")
    assert reason in report
    run.assert_not_called()


def test_simple_done_notification_without_pr_url_keeps_plain_message() -> None:
    response = _telegram_response()
    with mock.patch.dict(
        os.environ,
        {
            "SKELETON_TG_BOT": "telegram-bot-placeholder",
            "SKELETON_TG_CHAT": "telegram-chat-placeholder",
        },
        clear=True,
    ), mock.patch.object(
        runner.urllib.request, "urlopen", return_value=response
    ) as urlopen:
        runner.send_telegram_notification(
            runner.build_telegram_message(9, "DONE", "DONE report")
        )

    assert _request_payload(urlopen) == {
        "chat_id": ["telegram-chat-placeholder"],
        "text": [f"Repository: {runner.REPO}\nIssue: #9\nStatus: DONE"],
        "disable_web_page_preview": ["true"],
    }


def test_done_pr_report_builds_card_payload_from_runner_binding() -> None:
    card = {
        "text": "PR card",
        "buttons": [
            {
                "action": "details",
                "label": "Details",
                "callback_payload": {"action": "details"},
            }
        ],
    }
    with mock.patch.object(
        runner, "build_pr_ready_card_payload", return_value=card
    ) as build_card:
        localized_card = runner.build_done_pr_ready_card_payload(DONE_REPORT)

    assert localized_card is not None
    assert localized_card["text"] == (
        "PR: #123\n"
        "Надішліть номер PR у ChatGPT.\n"
        "Натисніть «Схвалити» лише після того, як ChatGPT скаже схвалити."
    )
    assert localized_card["buttons"][0]["label"] == "Деталі"

    build_card.assert_called_once_with(
        repo=runner.REPO,
        pr_number=123,
        head_sha=HEAD_SHA,
        changed_files=(
            "scripts/runner_poll_github_tasks.py",
            "docs/TELEGRAM_APPROVAL_BUTTONS.md",
        ),
        test_summary=runner.TELEGRAM_CARD_TEST_SUMMARY,
        risk_summary=runner.TELEGRAM_CARD_RISK_SUMMARY,
        pr_url=PR_URL,
    )


def test_done_pr_card_hides_technical_details_from_operator_text() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None

    text = str(card["text"])
    assert text == (
        "PR: #123\n"
        "Надішліть номер PR у ChatGPT.\n"
        "Натисніть «Схвалити» лише після того, як ChatGPT скаже схвалити."
    )
    assert HEAD_SHA not in text
    assert "scripts/runner_poll_github_tasks.py" not in text
    assert "docs/TELEGRAM_APPROVAL_BUTTONS.md" not in text
    assert "Skeleton task completed" not in text
    assert "Recommended action" not in text
    assert "Ця кнопка нічого не деплоїть" not in text


def test_done_pr_card_keeps_technical_details_in_payload() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None

    assert card["head_sha"] == HEAD_SHA
    assert card["changed_files"] == [
        "docs/TELEGRAM_APPROVAL_BUTTONS.md",
        "scripts/runner_poll_github_tasks.py",
    ]
    assert card["test_summary"] == runner.TELEGRAM_CARD_TEST_SUMMARY
    assert card["risk_summary"] == runner.TELEGRAM_CARD_RISK_SUMMARY


def test_inline_keyboard_has_pr_review_buttons_when_binding_is_reliable() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None

    reply_markup = runner.card_payload_to_inline_keyboard(card)
    buttons = [row[0] for row in reply_markup["inline_keyboard"]]
    assert [button["text"] for button in buttons] == [
        "Схвалити",
        "Відхилити",
        "Деталі",
        "Відкрити PR",
    ]
    assert [button["action"] for button in card["buttons"]] == [
        "approve",
        "reject",
        "details",
        "open_pr",
    ]
    assert buttons[-1]["url"] == PR_URL
    assert all(
        len(button["callback_data"].encode("utf-8"))
        <= runner.TELEGRAM_CALLBACK_DATA_LIMIT
        for button in buttons
        if "callback_data" in button
    )


def test_callback_data_carries_action_pr_number_and_head_marker() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None

    reply_markup = runner.card_payload_to_inline_keyboard(card)
    callback_values = [
        row[0]["callback_data"]
        for row in reply_markup["inline_keyboard"]
        if "callback_data" in row[0]
    ]

    assert callback_values
    assert all(value.startswith("tpr1:") for value in callback_values)
    assert any(value.startswith("tpr1:approve:p123:aaaaaaaa:") for value in callback_values)
    assert any(value.startswith("tpr1:reject:p123:aaaaaaaa:") for value in callback_values)
    assert all(":p123:aaaaaaaa:" in value for value in callback_values)


def test_approve_reject_buttons_require_reliable_sha_and_changed_files() -> None:
    report = f"DONE: ok\n\nDraft PR: {PR_URL}"

    card = runner.build_done_pr_ready_card_payload(report)
    assert card is not None
    reply_markup = runner.card_payload_to_inline_keyboard(card)
    buttons = [row[0] for row in reply_markup["inline_keyboard"]]

    assert [button["text"] for button in buttons] == ["Деталі", "Відкрити PR"]
    assert str(card["text"]).startswith("PR: #123\n")


def test_send_telegram_notification_posts_reply_markup_for_card() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None
    reply_markup = runner.card_payload_to_inline_keyboard(card)
    response = _telegram_response()

    with mock.patch.dict(
        os.environ,
        {
            "SKELETON_TG_BOT": "telegram-bot-placeholder",
            "SKELETON_TG_CHAT": "telegram-chat-placeholder",
        },
        clear=True,
    ), mock.patch.object(
        runner.urllib.request, "urlopen", return_value=response
    ) as urlopen:
        runner.send_telegram_notification(str(card["text"]), reply_markup)

    payload = _request_payload(urlopen)
    assert json.loads(payload["reply_markup"][0]) == reply_markup
    assert payload["text"] == [card["text"]]


def test_send_telegram_notification_without_env_makes_no_network_call() -> None:
    card = runner.build_done_pr_ready_card_payload(DONE_REPORT)
    assert card is not None

    with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
        runner.urllib.request, "urlopen"
    ) as urlopen:
        runner.send_telegram_notification(
            str(card["text"]), runner.card_payload_to_inline_keyboard(card)
        )

    urlopen.assert_not_called()


def test_done_pr_card_success_sends_reply_markup() -> None:
    card = {"text": "PR ready card", "buttons": []}
    reply_markup = {"inline_keyboard": []}

    with mock.patch.object(
        runner, "should_notify_task_finished", return_value=True
    ), mock.patch.object(
        runner, "build_done_pr_ready_card_payload", return_value=card
    ), mock.patch.object(
        runner, "card_payload_to_inline_keyboard", return_value=reply_markup
    ), mock.patch.object(runner, "send_telegram_notification") as send:
        runner.notify_task_finished(129, "DONE", DONE_REPORT)

    send.assert_called_once_with("PR ready card", reply_markup)


def test_done_pr_card_build_failure_falls_back_to_plain_done() -> None:
    with mock.patch.object(
        runner, "should_notify_task_finished", return_value=True
    ), mock.patch.object(
        runner,
        "build_done_pr_ready_card_payload",
        side_effect=RuntimeError("telegram-bot-token-must-not-leak"),
    ), mock.patch.object(runner, "send_telegram_notification") as send:
        runner.notify_task_finished(129, "DONE", DONE_REPORT)

    send.assert_called_once_with(_plain_done_message())
    assert "telegram-bot-token-must-not-leak" not in send.call_args.args[0]


def test_done_pr_reply_markup_send_failure_falls_back_to_plain_done() -> None:
    card = {"text": "PR ready card", "buttons": []}
    reply_markup = {"inline_keyboard": []}

    with mock.patch.object(
        runner, "should_notify_task_finished", return_value=True
    ), mock.patch.object(
        runner, "build_done_pr_ready_card_payload", return_value=card
    ), mock.patch.object(
        runner, "card_payload_to_inline_keyboard", return_value=reply_markup
    ), mock.patch.object(
        runner,
        "send_telegram_notification",
        side_effect=(RuntimeError("reply_markup send failed"), None),
    ) as send:
        runner.notify_task_finished(129, "DONE", DONE_REPORT)

    assert send.call_args_list == [
        mock.call("PR ready card", reply_markup),
        mock.call(_plain_done_message()),
    ]


def test_pr_card_build_does_not_execute_merge_or_reject_side_effects() -> None:
    card = {"text": "PR ready card", "buttons": []}
    with mock.patch.object(
        runner, "should_notify_task_finished", return_value=True
    ), mock.patch.object(
        runner, "build_done_pr_ready_card_payload", return_value=card
    ), mock.patch.object(runner, "run_command") as run_command, mock.patch.object(
        runner, "send_telegram_notification"
    ) as send:
        runner.notify_task_finished(129, "DONE", DONE_REPORT)

    run_command.assert_not_called()
    send.assert_called_once()


def _maintenance_issue(task_id: str | None, task_body: str = "") -> dict[str, object]:
    lines = ["Mode: RUNTIME_MAINTENANCE_TASK"]
    if task_id is not None:
        lines.append(f"Maintenance Task ID: {task_id}")
    if task_body:
        lines.extend(("", "```task", task_body, "```"))
    return {"number": 145, "title": "Runner maintenance", "body": "\n".join(lines)}


def _successful_maintenance_command(
    command: list[str], cwd: str | None = None
) -> tuple[int, str]:
    del cwd
    if command[:5] == ["sudo", "-n", "systemctl", "show", "--property=Result"]:
        return 0, "success\n"
    return 0, ""


def test_maintenance_task_bypasses_codex() -> None:
    report = (
        "DONE: Runner host maintenance task completed.\n"
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "success_criteria=met"
    )
    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "dispatch_runtime_maintenance_task", return_value=report
    ) as dispatch, mock.patch.object(
        runner, "prepare_issue_worktree"
    ) as prepare_worktree, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(
            _maintenance_issue(
                runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME, "Task: use Codex"
            )
        )

    dispatch.assert_called_once()
    prepare_worktree.assert_not_called()
    run_codex.assert_not_called()
    assert set_label.call_args_list == [
        mock.call(145, runner.LABEL_READY, runner.LABEL_RUNNING),
        mock.call(145, runner.LABEL_RUNNING, runner.LABEL_DONE),
    ]


def test_missing_maintenance_task_id_is_blocked() -> None:
    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(_maintenance_issue(None))

    block.assert_called_once_with(
        145,
        "Runtime maintenance task id is missing. Use `Maintenance Task ID:`.",
    )
    run_codex.assert_not_called()


def test_unknown_maintenance_task_is_blocked() -> None:
    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(_maintenance_issue("restart_everything"))

    block.assert_called_once_with(
        145,
        "Runtime maintenance task id `restart_everything` is not allowlisted.",
    )
    run_codex.assert_not_called()


def test_blocked_maintenance_output_is_not_labeled_runner_done() -> None:
    report = (
        "DONE: mislabeled maintenance report\n"
        "BLOCKED: step failed\n"
        "success_criteria=met"
    )
    with mock.patch.object(
        runner, "dispatch_runtime_maintenance_task", return_value=report
    ), mock.patch.object(runner, "post_issue_comment"), mock.patch.object(
        runner, "notify_task_finished"
    ) as notify, mock.patch.object(runner, "set_issue_label") as set_label:
        runner.process_runtime_maintenance_issue(
            145, runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME, str(runner.ROOT)
        )

    set_label.assert_called_once_with(145, runner.LABEL_RUNNING, runner.LABEL_BLOCKED)
    notify.assert_called_once_with(145, "BLOCKED", report)


def test_not_met_maintenance_output_is_not_labeled_runner_done() -> None:
    report = (
        "DONE: maintenance step returned\n"
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "success_criteria=not_met"
    )
    with mock.patch.object(
        runner, "dispatch_runtime_maintenance_task", return_value=report
    ), mock.patch.object(runner, "post_issue_comment"), mock.patch.object(
        runner, "notify_task_finished"
    ) as notify, mock.patch.object(runner, "set_issue_label") as set_label:
        runner.process_runtime_maintenance_issue(
            145, runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME, str(runner.ROOT)
        )

    set_label.assert_called_once_with(145, runner.LABEL_RUNNING, runner.LABEL_BLOCKED)
    notify.assert_called_once_with(145, "BLOCKED", report)


def test_maintenance_privileged_commands_are_non_interactive() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("DONE:")
    privileged_commands = [
        call.args[0] for call in run.call_args_list if call.args[0][0] == "sudo"
    ]
    assert privileged_commands
    assert all(command[:2] == ["sudo", "-n"] for command in privileged_commands)


def test_copied_callback_units_are_owned_by_root_and_read_only() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    service_unit = f"/etc/systemd/system/{runner.TELEGRAM_CALLBACK_POLLER_SERVICE}"
    timer_unit = f"/etc/systemd/system/{runner.TELEGRAM_CALLBACK_POLLER_TIMER}"

    assert report.startswith("DONE:")
    assert ["sudo", "-n", "chown", "root:root", service_unit] in commands
    assert ["sudo", "-n", "chown", "root:root", timer_unit] in commands
    assert ["sudo", "-n", "chmod", "0644", service_unit] in commands
    assert ["sudo", "-n", "chmod", "0644", timer_unit] in commands


def test_local_callback_config_task_maintains_only_callback_hmac_setting() -> None:
    with mock.patch.object(runner, "run_command", return_value=(0, "")) as run:
        report = runner.dispatch_runtime_maintenance_task(
            runner.ENSURE_TELEGRAM_CALLBACK_LOCAL_CONFIG, str(runner.ROOT)
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "step=verify_callback_hmac_secret status=done" in report
    assert ["sudo", "-n", "touch", runner.TELEGRAM_CALLBACK_LOCAL_CONFIG] in commands
    assert [
        "sudo",
        "-n",
        "chown",
        "root:root",
        runner.TELEGRAM_CALLBACK_LOCAL_CONFIG,
    ] in commands
    assert [
        "sudo",
        "-n",
        "chmod",
        "0600",
        runner.TELEGRAM_CALLBACK_LOCAL_CONFIG,
    ] in commands
    python_commands = [
        command for command in commands if command[2:4] == ["python3", "-c"]
    ]
    assert len(python_commands) == 2
    assert all(
        command[-2:]
        == [
            runner.TELEGRAM_CALLBACK_LOCAL_CONFIG,
            runner.TELEGRAM_CALLBACK_HMAC_ENV,
        ]
        for command in python_commands
    )


def test_local_callback_config_task_reports_blocked_on_verification_failure() -> None:
    def fail_config_verification(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        if command[-2:] == [
            runner.TELEGRAM_CALLBACK_LOCAL_CONFIG,
            runner.TELEGRAM_CALLBACK_HMAC_ENV,
        ] and runner._VERIFY_CALLBACK_HMAC_SCRIPT in command:
            return 1, "local config value must not be reported"
        return 0, ""

    with mock.patch.object(runner, "run_command", side_effect=fail_config_verification):
        report = runner.ensure_telegram_callback_local_config()

    assert report.startswith("BLOCKED:")
    assert "step=verify_callback_hmac_secret status=failed exit_code=1" in report
    assert "local config value" not in report


def test_sync_task_uses_only_allowed_service_names() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("DONE:")
    systemctl_commands = [
        call.args[0]
        for call in run.call_args_list
        if call.args[0][:3] == ["sudo", "-n", "systemctl"]
    ]
    used_units = {
        value
        for command in systemctl_commands
        for value in command
        if value.endswith((".service", ".timer"))
    }
    assert used_units == {
        runner.TELEGRAM_CALLBACK_POLLER_SERVICE,
        runner.TELEGRAM_CALLBACK_POLLER_TIMER,
    }


def test_done_requires_callback_timer_active_verification() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert "step=verify_callback_timer_active status=done" in report
    assert [
        "sudo",
        "-n",
        "systemctl",
        "is-active",
        "--quiet",
        runner.TELEGRAM_CALLBACK_POLLER_TIMER,
    ] in commands


def test_timer_verification_failure_reports_blocked() -> None:
    def fail_timer_verification(command: list[str], cwd: str | None = None) -> tuple[int, str]:
        if command[:5] == ["sudo", "-n", "systemctl", "is-active", "--quiet"]:
            return 3, ""
        return _successful_maintenance_command(command, cwd)

    with mock.patch.object(runner, "run_command", side_effect=fail_timer_verification):
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("BLOCKED:")
    assert "step=verify_callback_timer_active status=failed exit_code=3" in report
    assert "success_criteria=not_met" in report


def test_done_requires_callback_service_success_verification() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ):
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("DONE:")
    assert "step=verify_callback_service_result status=done" in report


def test_service_verification_failure_reports_blocked() -> None:
    def fail_service_verification(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        if command[:5] == ["sudo", "-n", "systemctl", "show", "--property=Result"]:
            return 0, "failed\n"
        return _successful_maintenance_command(command, cwd)

    with mock.patch.object(runner, "run_command", side_effect=fail_service_verification):
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("BLOCKED:")
    assert "step=verify_callback_service_result status=failed" in report


def test_failed_maintenance_verification_is_not_labeled_runner_done() -> None:
    report = (
        "BLOCKED: Runner host maintenance task did not complete.\n"
        "maintenance_task_id=sync_telegram_callback_poller_runtime\n"
        "step=verify_callback_timer_active status=failed exit_code=3\n"
        "success_criteria=not_met"
    )
    with mock.patch.object(
        runner, "dispatch_runtime_maintenance_task", return_value=report
    ), mock.patch.object(runner, "post_issue_comment"), mock.patch.object(
        runner, "notify_task_finished"
    ) as notify, mock.patch.object(runner, "set_issue_label") as set_label:
        runner.process_runtime_maintenance_issue(
            145, runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME, str(runner.ROOT)
        )

    set_label.assert_called_once_with(145, runner.LABEL_RUNNING, runner.LABEL_BLOCKED)
    notify.assert_called_once_with(145, "BLOCKED", report)


def test_maintenance_issue_body_does_not_execute_arbitrary_command() -> None:
    issue = _maintenance_issue(
        runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME,
        "sudo reboot\nsudo apt upgrade\nsystemctl restart unrelated.service",
    )
    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(runner, "set_issue_label"), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_command", side_effect=_successful_maintenance_command
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue, workdir=str(runner.ROOT))

    commands = [" ".join(call.args[0]) for call in run.call_args_list]
    assert all("reboot" not in command for command in commands)
    assert all("apt" not in command for command in commands)
    assert all("unrelated.service" not in command for command in commands)
    run_codex.assert_not_called()


def test_maintenance_report_does_not_include_command_output_token_values() -> None:
    token = "github-token-must-not-leak"
    with mock.patch.object(runner, "run_command", return_value=(1, token)):
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("BLOCKED:")
    assert token not in report
