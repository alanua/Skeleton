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


def _inspect_pr_issue_body(
    *,
    pr_number: int | str | None = 123,
    expected_head_sha: str | None = HEAD_SHA,
    repository: str = runner.REPO,
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
    pr: dict[str, object] = {
        "number": 123,
        "state": "open",
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "base": {
            "ref": "main",
            "sha": "b" * 40,
            "repo": {"full_name": runner.REPO},
        },
        "head": {"ref": "runner/issue-123", "sha": HEAD_SHA},
    }
    pr.update(updates.pop("pr", {}))
    state: dict[str, object] = {
        "pr": pr,
        "files": [{"filename": "scripts/runner_poll_github_tasks.py"}],
        "compare": {"status": "ahead", "ahead_by": 1, "behind_by": 0},
        "combined_status": {
            "state": "success",
            "statuses": [{"context": "pytest", "state": "success"}],
        },
        "check_runs": [],
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


def _project_tree_with(
    project_id: str,
    *,
    repo: str,
    runner_enabled: bool,
    planning_only: bool,
    codex_issue_worktree: bool,
    live_cross_repo: bool,
) -> dict[str, object]:
    project_tree = json.loads(json.dumps(runner.load_runner_project_tree()))
    project_tree["projects"][project_id] = {
        "repo": repo,
        "checkout_path": f"/home/agent/agent-dev/worktrees/{project_id}/main",
        "worktree_root": f"/home/agent/agent-dev/worktrees/{project_id}",
        "public": True,
        "runner_enabled": runner_enabled,
        "execution_modes": {
            "planning_only": planning_only,
            "codex_issue_worktree": codex_issue_worktree,
            "live_cross_repo": live_cross_repo,
        },
        "requires_explicit_approval_for_mode_change": True,
        "future_parallel_worktrees": False,
        "runtime_approval_required": True,
        "worktree_name_prefix": project_id.replace("_", "-"),
        "description": "Test project.",
    }
    return project_tree


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


def test_blocked_output_classifier_ignores_issue_438_transcript_tail() -> None:
    output = """Implemented the issue #421 two-file runner classifier patch.

Changed files:
- scripts/runner_poll_github_tasks.py
- tests/test_runner_poll_github_tasks.py

Test results:
- 126 passed
Reading additional input from stdin...
OpenAI Codex v0.125.0
--------
user
Task text mentions BLOCKED and runner:blocked in the echoed transcript.
"""

    final_answer = runner.final_codex_answer(output)

    assert final_answer.startswith("Implemented the issue #421")
    assert "Reading additional input from stdin" not in final_answer
    assert runner.blocked_output_marker(output) is None


def test_blocked_output_classifier_uses_final_done_deliverable_over_echoed_prompt() -> None:
    output = """Reading additional input from stdin...
OpenAI Codex v0.125.0
--------
user
Task instructions:
- A real final deliverable beginning with BLOCKED must still be classified as blocked.
- Do not weaken safety for true blocked reports.
--------
assistant
DONE: Codex completed successfully and produced file changes.

Changed files:
- scripts/runner_poll_github_tasks.py
- tests/test_runner_poll_github_tasks.py
- docs/RUNNER_MAINTENANCE_TASKS.md

Pytest output:
```
python3 -m pytest -q tests/test_runner_poll_github_tasks.py
115 passed

python3 -m pytest -q
503 passed, 3 skipped
```

No packages were installed during tests and no generic package-install capability was added.
"""

    assert runner.final_codex_answer(output).startswith("DONE:")
    assert runner.blocked_output_marker(output) is None



def test_final_codex_answer_prefers_useful_prefix_before_transcript_tail() -> None:
    marker = runner._BLOCKED_OUTPUT_MARKERS[0]
    output = (
        "DONE: useful work completed\n\n"
        "Changed files:\n"
        "- scripts/runner_poll_github_tasks.py\n\n"
        "Reading additional input from stdin...\n"
        "OpenAI Codex v0.125.0\n"
        "--------\n"
        "user\n"
        f"Echoed task text includes {marker} but it is not the final answer.\n"
    )

    final_answer = runner.final_codex_answer(output)

    assert final_answer.startswith("DONE: useful work completed")
    assert "Reading additional input from stdin" not in final_answer
    assert runner.blocked_output_marker(output) is None


def test_blocked_output_classifier_ignores_echoed_prompt_inside_codex_output_block() -> None:
    output = """DONE: Codex completed successfully with no file changes.

Codex output:
```
Task instructions:
- A real final deliverable beginning with BLOCKED must still be classified as blocked.
- BLOCKED: example text from the original prompt.
```
"""

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


def test_runner_report_status_blocks_file_change_done_with_placeholder_pr_url() -> None:
    report = DONE_REPORT.replace(PR_URL, "{PR_URL}")

    assert runner.extract_pr_url(report) is None
    assert runner.runner_report_status(report) == "BLOCKED"


def test_blocked_final_report_replaces_placeholder_pr_url() -> None:
    report = runner.blocked_final_report(DONE_REPORT.replace(PR_URL, "{PR_URL}"))

    assert "{PR_URL}" not in report
    assert "Draft PR: none" in report


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


def test_post_issue_comment_replaces_placeholder_pr_url() -> None:
    with mock.patch.object(runner, "run_command", return_value=(0, "")) as run:
        runner.post_issue_comment(9, "DONE\nDraft PR: {PR_URL}\nPR: {PR_URL}")

    command = run.call_args.args[0]
    body = command[command.index("--body") + 1]
    assert "{PR_URL}" not in body
    assert "Draft PR: none" in body
    assert "PR: none" in body


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
            Path("/home/agent/agent-dev/worktrees/bauclock")
        )
        assert runner.target_repository_worktree_root("alanua/Lavalamp") == (
            Path("/home/agent/agent-dev/worktrees/lavalamp")
        )
        assert runner.target_repository_checkout_path("alanua/bauclock") == (
            Path("/home/agent/agent-dev/worktrees/bauclock/main")
        )
        assert runner.target_repository_checkout_path("alanua/Lavalamp") == (
            Path("/home/agent/agent-dev/worktrees/lavalamp/main")
        )
        assert bauclock_path == (
            Path("/home/agent/agent-dev/worktrees/bauclock") / "issue-912"
        )
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
            "alanua/bauclock", Path("/home/agent/agent-dev/worktrees/skeleton/issue-912")
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
    run_codex.assert_called_once_with(
        "Do it", str(issue_path), runner.RunnerTask(content="Do it")
    )
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


def test_runner_task_accepts_allowlisted_target_project() -> None:
    task, reason = runner.extract_runner_task(
        "Target Project: bauclock\n\n```task\nDo it\n```"
    )

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        target_project="bauclock",
        has_target_project_metadata=True,
        target_repository="alanua/bauclock",
    )


def test_codex_task_prompt_includes_selected_project_context() -> None:
    prompt = runner.build_codex_task_prompt(
        "Return selected project.",
        "/tmp/worktree",
        runner.RunnerTask(
            content="Return selected project.",
            target_project="bauclock",
            has_target_project_metadata=True,
            target_repository="alanua/bauclock",
        ),
    )

    assert "Selected Project: bauclock" in prompt
    assert "Selected Repository: alanua/bauclock" in prompt


def test_runner_task_accepts_matching_target_project_and_repository() -> None:
    task, reason = runner.extract_runner_task(
        "Target Project: lavalamp\n"
        "Target Repository: alanua/Lavalamp\n\n"
        "```task\nDo it\n```"
    )

    assert reason is None
    assert task == runner.RunnerTask(
        content="Do it",
        target_project="lavalamp",
        has_target_project_metadata=True,
        target_repository="alanua/Lavalamp",
        has_target_repository_metadata=True,
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


def test_process_issue_blocks_unknown_target_project_before_claim() -> None:
    issue = {
        "number": 144,
        "title": "Target project metadata",
        "body": "Target Project: unknown\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "Target project `unknown` is not allowlisted" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_mismatched_target_project_and_repository_before_claim() -> None:
    issue = {
        "number": 145,
        "title": "Target project mismatch",
        "body": (
            "Target Project: bauclock\n"
            "Target Repository: alanua/Lavalamp\n\n"
            "```task\nDo it\n```"
        ),
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "resolve to different PROJECT_TREE entries" in block.call_args.args[1]
    set_label.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_runs_target_project_bauclock_in_local_worktree(
    tmp_path: Path,
) -> None:
    issue_path = tmp_path / "bauclock" / "issue-146"
    issue = {
        "number": 146,
        "title": "Target project bauclock",
        "body": "Target Project: bauclock\n\n```task\nDo it\n```",
    }

    with mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(
        runner,
        "prepare_target_repository_issue_worktree",
        return_value=(0, "ready", issue_path),
    ) as prepare_target, mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_success"
    ) as finalize_success, mock.patch.object(
        runner, "finalize_local_worktree_success", return_value="DONE local report"
    ) as finalize_local, mock.patch.object(
        runner, "cleanup_target_repository_issue_worktree", return_value=(0, "")
    ) as cleanup_target, mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ):
        runner.process_issue(issue)

    expected_task = runner.RunnerTask(
        content="Do it",
        target_project="bauclock",
        has_target_project_metadata=True,
        target_repository="alanua/bauclock",
    )
    prepare_branch.assert_not_called()
    prepare_target.assert_called_once_with("alanua/bauclock", 146)
    run_codex.assert_called_once_with("Do it", str(issue_path), expected_task)
    finalize_success.assert_not_called()
    finalize_local.assert_called_once_with(str(issue_path), "codex output", expected_task)
    cleanup_target.assert_called_once_with("alanua/bauclock", 146)
    assert comment.call_args.args[1] == "DONE local report\nTarget Project: bauclock"


def test_process_issue_blocks_target_project_lavalamp_planning_only_before_codex() -> None:
    issue = {
        "number": 147,
        "title": "Target project lavalamp",
        "body": "Target Project: lavalamp\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "planning-only" in block.call_args.args[1]
    assert block.call_args.kwargs["runner_task"] == runner.RunnerTask(
        content="Do it",
        target_project="lavalamp",
        has_target_project_metadata=True,
        target_repository="alanua/Lavalamp",
    )
    set_label.assert_not_called()
    prepare_branch.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_runs_target_project_skeleton_normally(tmp_path: Path) -> None:
    coordinator = tmp_path / "repo"
    issue_path = tmp_path / "worktree"
    coordinator.mkdir()
    issue = {
        "number": 148,
        "title": "Target project skeleton",
        "body": "Target Project: skeleton\n\n```task\nDo it\n```",
    }

    with mock.patch.object(
        runner, "prepare_issue_branch", return_value=(0, "", issue_path)
    ) as prepare, mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_success", return_value="DONE report"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "cleanup_issue_worktree", return_value=(0, "")
    ):
        runner.process_issue(issue, workdir=str(coordinator))

    prepare.assert_called_once_with(148, str(coordinator))
    run_codex.assert_called_once_with(
        "Do it",
        str(issue_path),
        runner.RunnerTask(
            content="Do it",
            target_project="skeleton",
            has_target_project_metadata=True,
            target_repository="alanua/Skeleton",
        ),
    )
    assert comment.call_args.args[1] == "DONE report\nTarget Project: skeleton"


def test_process_issue_does_not_execute_allowlisted_cross_repo_target_yet() -> None:
    issue = {
        "number": 143,
        "title": "Target repository stage 1",
        "body": "Target Repository: alanua/Lavalamp\n\n```task\nDo it\n```",
    }

    with mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "planning-only" in block.call_args.args[1]
    set_label.assert_not_called()
    prepare_branch.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_blocks_runner_disabled_project_before_codex() -> None:
    issue = {
        "number": 149,
        "title": "Disabled target project",
        "body": "Target Project: disabled_public\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with(
        "disabled_public",
        repo="alanua/Disabled",
        runner_enabled=False,
        planning_only=False,
        codex_issue_worktree=True,
        live_cross_repo=False,
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "Runner is disabled" in block.call_args.args[1]
    set_label.assert_not_called()
    prepare_branch.assert_not_called()
    run_codex.assert_not_called()


def test_process_issue_runs_non_skeleton_codex_worktree_mode_locally(
    tmp_path: Path,
) -> None:
    issue_path = tmp_path / "codex-other" / "issue-150"
    issue = {
        "number": 150,
        "title": "Non-skeleton codex worktree",
        "body": "Target Project: codex_other\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with(
        "codex_other",
        repo="alanua/CodexOther",
        runner_enabled=True,
        planning_only=False,
        codex_issue_worktree=True,
        live_cross_repo=False,
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(
        runner,
        "prepare_target_repository_issue_worktree",
        return_value=(0, "ready", issue_path),
    ) as prepare_target, mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex output")
    ) as run_codex, mock.patch.object(
        runner, "finalize_local_worktree_success", return_value="DONE local report"
    ) as finalize_local, mock.patch.object(
        runner, "cleanup_target_repository_issue_worktree", return_value=(0, "")
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ):
        runner.process_issue(issue)

    prepare_branch.assert_not_called()
    prepare_target.assert_called_once_with("alanua/CodexOther", 150)
    run_codex.assert_called_once()
    finalize_local.assert_called_once()


def test_process_issue_blocks_live_cross_repo_mode_before_codex() -> None:
    issue = {
        "number": 151,
        "title": "Live cross repo",
        "body": "Target Project: live_other\n\n```task\nDo it\n```",
    }
    project_tree = _project_tree_with(
        "live_other",
        repo="alanua/LiveOther",
        runner_enabled=True,
        planning_only=False,
        codex_issue_worktree=False,
        live_cross_repo=True,
    )

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner, "block_issue") as block, mock.patch.object(
        runner, "set_issue_label"
    ) as set_label, mock.patch.object(
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(runner, "run_codex_task") as run_codex:
        runner.process_issue(issue)

    assert "requires a separate PR" in block.call_args.args[1]
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


def test_telegram_message_omits_placeholder_pr_url() -> None:
    message = runner.build_telegram_message(
        9, "BLOCKED", DONE_REPORT.replace(PR_URL, "{PR_URL}")
    )

    assert "{PR_URL}" not in message
    assert "PR:" not in message


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


def _maintenance_issue(
    task_id: str | None, task_body: str = "", metadata: str = ""
) -> dict[str, object]:
    lines = ["Mode: RUNTIME_MAINTENANCE_TASK"]
    if task_id is not None:
        lines.append(f"Maintenance Task ID: {task_id}")
    if metadata:
        lines.extend(metadata.splitlines())
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


def _project_tree_for_checkout(project_id: str, checkout_path: Path) -> dict[str, object]:
    project_tree = _project_tree_with(
        project_id,
        repo="alanua/CheckoutTest",
        runner_enabled=True,
        planning_only=False,
        codex_issue_worktree=True,
        live_cross_repo=False,
    )
    project_tree["projects"][project_id]["checkout_path"] = str(checkout_path)
    return project_tree


def _checkout_issue_body(project_id: str = "checkout_test") -> str:
    return "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            f"Maintenance Task ID: {runner.CHECK_PROJECT_CHECKOUT}",
            f"Target Project: {project_id}",
        )
    )


def _ensure_checkout_issue_body(project_id: str = "checkout_test") -> str:
    return "\n".join(
        (
            "Mode: RUNTIME_MAINTENANCE_TASK",
            f"Maintenance Task ID: {runner.ENSURE_PROJECT_CHECKOUT}",
            f"Target Project: {project_id}",
        )
    )


def _skeleton_freshness_issue_body(task_body: str = "") -> str:
    lines = [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {runner.CHECK_SKELETON_FRESHNESS}",
    ]
    if task_body:
        lines.extend(("", "```task", task_body, "```"))
    return "\n".join(lines)


def _project_tree_for_skeleton_checkout(checkout_path: Path) -> dict[str, object]:
    project_tree = json.loads(json.dumps(runner.load_runner_project_tree()))
    project_tree["projects"]["skeleton"]["checkout_path"] = str(checkout_path)
    project_tree["projects"]["skeleton"]["repo"] = runner.REPO
    return project_tree


def _validate_pr_issue_body(
    *,
    pr_number: int | str | None = 123,
    expected_head_sha: str | None = HEAD_SHA,
    profile: str | None = "full_pytest",
    task_body: str = "",
) -> str:
    lines = [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {runner.VALIDATE_PR_BRANCH}",
    ]
    if pr_number is not None:
        lines.append(f"Pull Request: {pr_number}")
    if expected_head_sha is not None:
        lines.append(f"Expected Head SHA: {expected_head_sha}")
    if profile is not None:
        lines.append(f"Validation Profile: {profile}")
    if task_body:
        lines.extend(("", "```task", task_body, "```"))
    return "\n".join(lines)


def _preflight_pr_issue_body(
    *,
    pr_number: int | str | None = 123,
    expected_head_sha: str | None = HEAD_SHA,
    task_body: str = "",
) -> str:
    lines = [
        "Mode: RUNTIME_MAINTENANCE_TASK",
        f"Maintenance Task ID: {runner.PREFLIGHT_PR_REFRESH}",
    ]
    if pr_number is not None:
        lines.append(f"Pull Request: {pr_number}")
    if expected_head_sha is not None:
        lines.append(f"Expected Head SHA: {expected_head_sha}")
    if task_body:
        lines.extend(("", "```task", task_body, "```"))
    return "\n".join(lines)


def _pr_validation_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "number": 123,
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": "runner-test-branch",
        "headRefOid": HEAD_SHA,
    }
    state.update(updates)
    return state


def _preflight_pr_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "number": 123,
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": "runner-test-branch",
        "headRefOid": HEAD_SHA,
        "headRepository": {
            "name": "Skeleton",
            "nameWithOwner": runner.REPO,
            "owner": {"login": "alanua"},
        },
        "headRepositoryOwner": {"login": "alanua"},
        "files": [{"path": "new_runner_file.py"}],
    }
    state.update(updates)
    return state


def _preflight_compare_state(**updates: object) -> dict[str, object]:
    state: dict[str, object] = {
        "status": "ahead",
        "ahead_by": 1,
        "behind_by": 0,
    }
    state.update(updates)
    return state


def _safe_checkout_path(name: str) -> Path:
    return runner.RUNNER_PROJECT_CHECKOUT_BASE / "worktrees" / name


def test_check_project_checkout_missing_target_project_blocks() -> None:
    report = runner.check_project_checkout(
        "Mode: RUNTIME_MAINTENANCE_TASK\n"
        f"Maintenance Task ID: {runner.CHECK_PROJECT_CHECKOUT}"
    )

    assert report.startswith("BLOCKED:")
    assert "reason=missing_target_project" in report


def test_check_project_checkout_unknown_target_project_blocks() -> None:
    report = runner.check_project_checkout(_checkout_issue_body("unknown"))

    assert report.startswith("BLOCKED:")
    assert "reason=target_project_unknown" in report


def test_check_project_checkout_unsafe_path_blocks() -> None:
    project_tree = _project_tree_for_checkout(
        "checkout_test", Path("/tmp/checkout-test")
    )
    with mock.patch.object(runner, "load_runner_project_tree", return_value=project_tree):
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_unsafe" in report


def test_check_project_checkout_path_traversal_blocks() -> None:
    project_tree = _project_tree_for_checkout(
        "checkout_test", Path("/home/agent/agent-dev/../checkout-test")
    )
    with mock.patch.object(runner, "load_runner_project_tree", return_value=project_tree):
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_traversal" in report


def test_check_project_checkout_missing_checkout_path_blocks() -> None:
    checkout_path = _safe_checkout_path("missing-checkout-for-maintenance-test")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(runner, "load_runner_project_tree", return_value=project_tree):
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_missing" in report


def test_check_project_checkout_missing_git_blocks_under_runner_base() -> None:
    checkout_path = _safe_checkout_path("checkout-without-git")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: True, checkout_path / ".git": False}
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists:
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_git_missing" in report


def test_check_project_checkout_wrong_remote_blocks() -> None:
    checkout_path = _safe_checkout_path("checkout-wrong-remote")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", return_value=(0, "https://github.com/alanua/Wrong.git\n")
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=verify_origin_remote status=failed" in report


def test_check_project_checkout_matching_remote_reports_done() -> None:
    checkout_path = _safe_checkout_path("checkout-matching-remote")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner,
        "run_command",
        return_value=(0, "git@github.com:alanua/CheckoutTest.git\n"),
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_project_checkout(_checkout_issue_body())

    assert report.startswith("DONE:")
    assert "success_criteria=met" in report
    run.assert_called_once_with(
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]
    )


def test_check_project_checkout_task_never_runs_mutating_git_or_gh_pr() -> None:
    issue = _maintenance_issue(
        runner.CHECK_PROJECT_CHECKOUT,
        "git clone bad\n"
        "git pull\n"
        "git fetch\n"
        "git push\n"
        "gh pr create\n"
        "sudo chmod 777 /tmp/nope",
        metadata="Target Project: checkout_test",
    )
    checkout_path = _safe_checkout_path("checkout-task-never-runs")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner,
        "run_command",
        return_value=(0, "https://github.com/alanua/CheckoutTest.git\n"),
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        runner.process_issue(issue, workdir=str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]
    ]


def test_check_skeleton_freshness_is_allowlisted() -> None:
    assert runner.CHECK_SKELETON_FRESHNESS == "check_skeleton_freshness"
    assert runner.CHECK_SKELETON_FRESHNESS in runner.RUNTIME_MAINTENANCE_TASK_IDS


def test_check_skeleton_freshness_reports_done_with_bounded_status_queries() -> None:
    checkout_path = _safe_checkout_path("skeleton-fresh")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    github_main_sha = "b" * 40
    checkout_head_sha = "a" * 40

    def run_freshness_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "fetch",
            "--prune",
            "origin",
            "main",
        ]:
            return 0, "raw fetch output must not appear"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{checkout_head_sha}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{github_main_sha}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "ls-remote",
            "origin",
            "refs/heads/main",
        ]:
            return 0, f"{github_main_sha}\trefs/heads/main\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "merge-base",
            "--is-ancestor",
            checkout_head_sha,
            github_main_sha,
        ]:
            return 0, ""
        if command == ["gh", "pr", "list", "--repo", runner.REPO, "--state", "open"]:
            return 0, "123\tFix runner\n124\tRetest\n"
        if command == ["gh", "issue", "list", "--repo", runner.REPO, "--state", "open"]:
            return 0, "533\tFreshness task\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_freshness_command
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.dispatch_runtime_maintenance_task(
            runner.CHECK_SKELETON_FRESHNESS,
            str(runner.ROOT),
            _skeleton_freshness_issue_body(
                "git push\nsudo env\ngh pr merge 123\ncodex exec unsafe"
            ),
        )

    assert report.startswith("DONE:")
    assert "maintenance_task_id=check_skeleton_freshness" in report
    assert f"checkout_head_sha={checkout_head_sha}" in report
    assert f"github_main_sha={github_main_sha}" in report
    assert "github_main_source_of_truth=true" in report
    assert "checkout_sync_state=behind" in report
    assert "open_pull_requests_count=2" in report
    assert "open_issues_count=1" in report
    assert "NOTEBOOKLM_SOURCEPACK.md" in report
    assert "old_chats_and_old_branches_are_not_canon" in report
    assert "raw fetch output" not in report
    assert "Fix runner" not in report
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
        ["git", "-C", str(checkout_path), "fetch", "--prune", "origin", "main"],
        ["git", "-C", str(checkout_path), "rev-parse", "HEAD"],
        ["git", "-C", str(checkout_path), "rev-parse", "origin/main"],
        ["git", "-C", str(checkout_path), "ls-remote", "origin", "refs/heads/main"],
        [
            "git",
            "-C",
            str(checkout_path),
            "merge-base",
            "--is-ancestor",
            checkout_head_sha,
            github_main_sha,
        ],
        ["gh", "pr", "list", "--repo", runner.REPO, "--state", "open"],
        ["gh", "issue", "list", "--repo", runner.REPO, "--state", "open"],
    ]


def test_check_skeleton_freshness_unsafe_path_blocks_before_commands() -> None:
    project_tree = _project_tree_for_skeleton_checkout(Path("/tmp/Skeleton"))
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(runner, "run_command") as run:
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_unsafe" in report
    run.assert_not_called()


def test_check_skeleton_freshness_missing_git_blocks() -> None:
    checkout_path = _safe_checkout_path("skeleton-missing-git")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command"
    ) as run:
        path_exists.side_effect = lambda path: path == checkout_path
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_git_missing" in report
    run.assert_not_called()


def test_check_skeleton_freshness_origin_mismatch_blocks_without_raw_output() -> None:
    checkout_path = _safe_checkout_path("skeleton-wrong-origin")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", return_value=(0, "https://github.com/alanua/Wrong.git\n")
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "step=verify_origin_remote status=failed" in report
    assert "Wrong.git" not in report


def test_check_skeleton_freshness_github_query_failure_blocks_safely() -> None:
    checkout_path = _safe_checkout_path("skeleton-gh-query-fails")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)

    def run_freshness_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command[:4] == ["git", "-C", str(checkout_path), "fetch"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{HEAD_SHA}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{HEAD_SHA}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "ls-remote",
            "origin",
            "refs/heads/main",
        ]:
            return 0, f"{HEAD_SHA}\trefs/heads/main\n"
        if command == ["gh", "pr", "list", "--repo", runner.REPO, "--state", "open"]:
            return 1, "token must not leak"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_freshness_command
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "step=query_open_pull_requests status=failed exit_code=1" in report
    assert "token must not leak" not in report


def test_check_skeleton_freshness_unclassified_sync_state_blocks() -> None:
    checkout_path = _safe_checkout_path("skeleton-unclassified")
    project_tree = _project_tree_for_skeleton_checkout(checkout_path)
    github_main_sha = "b" * 40

    def run_freshness_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Skeleton.git\n"
        if command[:4] == ["git", "-C", str(checkout_path), "fetch"]:
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "rev-parse", "HEAD"]:
            return 0, f"{HEAD_SHA}\n"
        if command == ["git", "-C", str(checkout_path), "rev-parse", "origin/main"]:
            return 0, f"{github_main_sha}\n"
        if command == [
            "git",
            "-C",
            str(checkout_path),
            "ls-remote",
            "origin",
            "refs/heads/main",
        ]:
            return 0, f"{github_main_sha}\trefs/heads/main\n"
        if command[:5] == ["git", "-C", str(checkout_path), "merge-base", "--is-ancestor"]:
            return 128, "fatal output must not leak"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", side_effect=run_freshness_command
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.check_skeleton_freshness()

    assert report.startswith("BLOCKED:")
    assert "step=classify_checkout_behind status=failed exit_code=128" in report
    assert "fatal output" not in report


def test_ensure_project_checkout_missing_target_project_blocks() -> None:
    report = runner.ensure_project_checkout(
        "Mode: RUNTIME_MAINTENANCE_TASK\n"
        f"Maintenance Task ID: {runner.ENSURE_PROJECT_CHECKOUT}"
    )

    assert report.startswith("BLOCKED:")
    assert "reason=missing_target_project" in report


def test_ensure_project_checkout_unknown_target_project_blocks() -> None:
    report = runner.ensure_project_checkout(_ensure_checkout_issue_body("unknown"))

    assert report.startswith("BLOCKED:")
    assert "reason=target_project_unknown" in report


def test_ensure_project_checkout_unsafe_path_blocks() -> None:
    project_tree = _project_tree_for_checkout(
        "checkout_test", Path("/tmp/checkout-test")
    )
    with mock.patch.object(runner, "load_runner_project_tree", return_value=project_tree):
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_unsafe" in report


def test_ensure_project_checkout_path_traversal_blocks() -> None:
    project_tree = _project_tree_for_checkout(
        "checkout_test", Path("/home/agent/agent-dev/../checkout-test")
    )
    with mock.patch.object(runner, "load_runner_project_tree", return_value=project_tree):
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_path_traversal" in report


def test_ensure_project_checkout_existing_valid_reports_done_without_preparation() -> None:
    checkout_path = _safe_checkout_path("checkout-existing-valid")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner,
        "run_command",
        return_value=(0, "https://github.com/alanua/CheckoutTest.git\n"),
    ) as run:
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("DONE:")
    assert "step=prepare_checkout_parent" not in report
    assert "step=prepare_checkout" not in report
    run.assert_called_once_with(
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]
    )


def test_ensure_project_checkout_existing_missing_git_blocks() -> None:
    checkout_path = _safe_checkout_path("checkout-existing-missing-git")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: True, checkout_path / ".git": False}
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists:
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=checkout_git_missing" in report


def test_ensure_project_checkout_existing_wrong_remote_blocks() -> None:
    checkout_path = _safe_checkout_path("checkout-existing-wrong-remote")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        runner, "run_command", return_value=(0, "https://github.com/alanua/Wrong.git\n")
    ):
        path_exists.side_effect = lambda path: path in {
            checkout_path,
            checkout_path / ".git",
        }
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=verify_origin_remote status=failed" in report


def test_ensure_project_checkout_missing_checkout_prepares_parent_before_clone_and_uses_only_registry_repo_and_path() -> None:
    checkout_path = _safe_checkout_path("prepared-checkout")
    checkout_parent = checkout_path.parent
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: False, checkout_path / ".git": False}
    operations: list[str] = []

    def run_registered_command(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        del cwd
        if command == [
            "git",
            "clone",
            "https://github.com/alanua/CheckoutTest.git",
            str(checkout_path),
        ]:
            operations.append("clone")
            exists[checkout_path] = True
            exists[checkout_path / ".git"] = True
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            operations.append("verify_origin")
            return 0, "https://github.com/alanua/CheckoutTest.git\n"
        return 2, "unexpected command"

    def mkdir_registered_parent(
        path: Path, parents: bool = False, exist_ok: bool = False
    ) -> None:
        operations.append("mkdir")
        assert path == checkout_parent
        assert parents is True
        assert exist_ok is True

    body = (
        _ensure_checkout_issue_body()
        + "\n```task\n"
        + "Repo: https://github.com/evil/Repo.git\n"
        + "Path: /tmp/evil\n"
        + "git clone https://github.com/evil/Repo.git /tmp/evil\n"
        + "```"
    )
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path, "mkdir", autospec=True
    ) as path_mkdir, mock.patch.object(
        runner, "run_command", side_effect=run_registered_command
    ) as run:
        path_exists.side_effect = lambda path: exists.get(path, False)
        path_mkdir.side_effect = mkdir_registered_parent
        report = runner.ensure_project_checkout(body)

    assert report.startswith("DONE:")
    assert "step=prepare_checkout_parent status=done" in report
    assert "step=prepare_checkout status=done" in report
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        [
            "git",
            "clone",
            "https://github.com/alanua/CheckoutTest.git",
            str(checkout_path),
        ],
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
    ]
    path_mkdir.assert_called_once_with(checkout_parent, parents=True, exist_ok=True)
    assert operations == ["mkdir", "clone", "verify_origin"]


def test_ensure_project_checkout_parent_preparation_failure_blocks_safely() -> None:
    checkout_path = _safe_checkout_path("parent-preparation-fails")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: False}
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path,
        "mkdir",
        autospec=True,
        side_effect=OSError("must not leak parent mkdir failure"),
    ) as path_mkdir, mock.patch.object(runner, "run_command") as run:
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=prepare_checkout_parent status=failed" in report
    assert "reason=checkout_parent_prepare_failed" in report
    assert "must not leak" not in report
    path_mkdir.assert_called_once_with(
        checkout_path.parent, parents=True, exist_ok=True
    )
    run.assert_not_called()


def test_ensure_project_checkout_preparation_failure_blocks() -> None:
    checkout_path = _safe_checkout_path("clone-fails")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: False}
    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", return_value=(128, "clone failure must not leak")
    ):
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=prepare_checkout_parent status=done" in report
    assert "step=prepare_checkout status=failed exit_code=128" in report
    assert "clone failure" not in report


def test_ensure_project_checkout_remote_mismatch_after_preparation_blocks() -> None:
    checkout_path = _safe_checkout_path("prepared-wrong-remote")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: False, checkout_path / ".git": False}

    def run_registered_command(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        del cwd
        if command[:2] == ["git", "clone"]:
            exists[checkout_path] = True
            exists[checkout_path / ".git"] = True
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/Wrong.git\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_registered_command
    ):
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.ensure_project_checkout(_ensure_checkout_issue_body())

    assert report.startswith("BLOCKED:")
    assert "step=prepare_checkout_parent status=done" in report
    assert "step=prepare_checkout status=done" in report
    assert "step=verify_origin_remote status=failed" in report


def test_ensure_project_checkout_task_never_runs_forbidden_commands_or_codex() -> None:
    issue = _maintenance_issue(
        runner.ENSURE_PROJECT_CHECKOUT,
        "git pull\n"
        "git fetch\n"
        "git checkout main\n"
        "git push\n"
        "gh pr create\n"
        "codex exec unsafe",
        metadata="Target Project: checkout_test",
    )
    checkout_path = _safe_checkout_path("prepared-checkout-task")
    project_tree = _project_tree_for_checkout("checkout_test", checkout_path)
    exists = {checkout_path: False, checkout_path / ".git": False}

    def run_registered_command(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        del cwd
        if command[:2] == ["git", "clone"]:
            exists[checkout_path] = True
            exists[checkout_path / ".git"] = True
            return 0, ""
        if command == ["git", "-C", str(checkout_path), "remote", "get-url", "origin"]:
            return 0, "https://github.com/alanua/CheckoutTest.git\n"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "load_runner_project_tree", return_value=project_tree
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_command", side_effect=run_registered_command
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        path_exists.side_effect = lambda path: exists.get(path, False)
        runner.process_issue(issue, workdir=str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    command_words = [" ".join(command) for command in commands]
    assert commands == [
        [
            "git",
            "clone",
            "https://github.com/alanua/CheckoutTest.git",
            str(checkout_path),
        ],
        ["git", "-C", str(checkout_path), "remote", "get-url", "origin"],
    ]
    assert all(" pull" not in command for command in command_words)
    assert all(" fetch" not in command for command in command_words)
    assert all(" checkout " not in command for command in command_words)
    assert all(" push" not in command for command in command_words)
    assert all("gh pr" not in command for command in command_words)
    run_codex.assert_not_called()


def test_validate_pr_branch_missing_pr_number_blocks() -> None:
    report = runner.validate_pr_branch(_validate_pr_issue_body(pr_number=None))

    assert report.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_pull_request" in report


def test_preflight_pr_refresh_missing_pr_number_blocks() -> None:
    report = runner.preflight_pr_refresh(_preflight_pr_issue_body(pr_number=None))

    assert report.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_pull_request" in report


def test_preflight_pr_refresh_closed_pr_reports_manual_review() -> None:
    with mock.patch.object(
        runner,
        "_get_preflight_pr_refresh_state",
        return_value=_preflight_pr_state(state="CLOSED"),
    ), mock.patch.object(
        runner,
        "_get_preflight_compare_state",
        return_value=_preflight_compare_state(ahead_by=1, behind_by=0),
    ), mock.patch.object(
        runner, "_main_contains_path", return_value=False
    ):
        report = runner.preflight_pr_refresh(_preflight_pr_issue_body())

    assert report.startswith("DONE:")
    assert "pr_state=CLOSED" in report
    assert "next_action=manual review required" in report


def test_preflight_pr_refresh_head_sha_mismatch_blocks() -> None:
    with mock.patch.object(
        runner, "_get_preflight_pr_refresh_state", return_value=_preflight_pr_state()
    ), mock.patch.object(runner, "_get_preflight_compare_state") as compare:
        report = runner.preflight_pr_refresh(
            _preflight_pr_issue_body(expected_head_sha="b" * 40)
        )

    assert report.startswith("BLOCKED:")
    assert "reason=expected_head_sha_mismatch" in report
    compare.assert_not_called()


def test_preflight_pr_refresh_identical_to_main_reports_obsolete() -> None:
    with mock.patch.object(
        runner, "_get_preflight_pr_refresh_state", return_value=_preflight_pr_state()
    ), mock.patch.object(
        runner,
        "_get_preflight_compare_state",
        return_value=_preflight_compare_state(
            status="identical", ahead_by=0, behind_by=0
        ),
    ), mock.patch.object(
        runner, "_main_contains_path", return_value=True
    ):
        report = runner.preflight_pr_refresh(_preflight_pr_issue_body())

    assert report.startswith("DONE:")
    assert "compare_status=identical" in report
    assert "next_action=mark obsolete" in report


def test_preflight_pr_refresh_behind_only_new_files_recommends_fresh_pr() -> None:
    with mock.patch.object(
        runner, "_get_preflight_pr_refresh_state", return_value=_preflight_pr_state()
    ), mock.patch.object(
        runner,
        "_get_preflight_compare_state",
        return_value=_preflight_compare_state(
            status="diverged", ahead_by=1, behind_by=3
        ),
    ), mock.patch.object(
        runner, "_main_contains_path", return_value=False
    ):
        report = runner.preflight_pr_refresh(_preflight_pr_issue_body())

    assert report.startswith("DONE:")
    assert "compare_status=diverged" in report
    assert "files_on_main_count=0" in report
    assert "next_action=create fresh PR" in report


def test_preflight_pr_refresh_overlapping_main_files_requires_manual_review() -> None:
    with mock.patch.object(
        runner, "_get_preflight_pr_refresh_state", return_value=_preflight_pr_state()
    ), mock.patch.object(
        runner,
        "_get_preflight_compare_state",
        return_value=_preflight_compare_state(
            status="diverged", ahead_by=1, behind_by=2
        ),
    ), mock.patch.object(
        runner, "_main_contains_path", return_value=True
    ):
        report = runner.preflight_pr_refresh(_preflight_pr_issue_body())

    assert report.startswith("DONE:")
    assert "files_on_main_count=1" in report
    assert "next_action=manual review required" in report


def test_preflight_pr_refresh_open_current_pr_recommends_validate_and_merge() -> None:
    with mock.patch.object(
        runner, "_get_preflight_pr_refresh_state", return_value=_preflight_pr_state()
    ), mock.patch.object(
        runner,
        "_get_preflight_compare_state",
        return_value=_preflight_compare_state(status="ahead", ahead_by=1, behind_by=0),
    ), mock.patch.object(
        runner, "_main_contains_path", return_value=False
    ):
        report = runner.preflight_pr_refresh(_preflight_pr_issue_body())

    assert report.startswith("DONE:")
    assert "next_action=validate and merge" in report


def test_preflight_pr_refresh_task_makes_no_mutating_calls() -> None:
    issue = _maintenance_issue(
        runner.PREFLIGHT_PR_REFRESH,
        "git update-ref refs/heads/main HEAD\n"
        "git merge unsafe\n"
        "git push --force\n"
        "gh pr merge 123\n"
        "python3 -c 'open(\"/tmp/nope\", \"w\").write(\"x\")'",
        metadata="\n".join(
            (
                "Pull Request: 123",
                f"Expected Head SHA: {HEAD_SHA}",
            )
        ),
    )

    def run_preflight_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        del cwd
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_preflight_pr_state())
        if command[:2] == ["gh", "api"] and "compare" in command[2]:
            return 0, json.dumps(_preflight_compare_state(status="ahead"))
        if command[:4] == ["gh", "api", "--method", "GET"]:
            return 1, "not found"
        return 2, "unexpected command"

    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_command", side_effect=run_preflight_command
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue, workdir=str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    command_words = [" ".join(command) for command in commands]
    assert all("update-ref" not in command for command in command_words)
    assert all(" merge" not in command for command in command_words)
    assert all(" push" not in command for command in command_words)
    assert all("checkout" not in command for command in command_words)
    assert all("-c" not in command for command in command_words)
    assert all("open(" not in command for command in command_words)
    run_codex.assert_not_called()


def test_validate_pr_branch_invalid_pr_number_blocks() -> None:
    report = runner.validate_pr_branch(_validate_pr_issue_body(pr_number="abc"))

    assert report.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_pull_request" in report


def test_validate_pr_branch_unsupported_profile_blocks() -> None:
    report = runner.validate_pr_branch(_validate_pr_issue_body(profile="shell"))

    assert report.startswith("BLOCKED:")
    assert "reason=unsupported_validation_profile" in report


def test_validate_pr_branch_expected_head_sha_mismatch_blocks() -> None:
    with mock.patch.object(
        runner, "_get_pr_branch_validation_state", return_value=_pr_validation_state()
    ):
        report = runner.validate_pr_branch(
            _validate_pr_issue_body(expected_head_sha="b" * 40)
        )

    assert report.startswith("BLOCKED:")
    assert "reason=expected_head_sha_mismatch" in report


def test_validate_pr_branch_rejects_closed_or_non_main_prs() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_branch_validation_state",
        return_value=_pr_validation_state(state="CLOSED"),
    ):
        closed_report = runner.validate_pr_branch(_validate_pr_issue_body())
    with mock.patch.object(
        runner,
        "_get_pr_branch_validation_state",
        return_value=_pr_validation_state(baseRefName="develop"),
    ):
        base_report = runner.validate_pr_branch(_validate_pr_issue_body())

    assert closed_report.startswith("BLOCKED:")
    assert "reason=pr_not_open" in closed_report
    assert base_report.startswith("BLOCKED:")
    assert "reason=pr_base_not_main" in base_report


def test_validate_pr_branch_unsafe_validation_path_blocks(tmp_path: Path) -> None:
    with mock.patch.object(
        runner, "_get_pr_branch_validation_state", return_value=_pr_validation_state()
    ), mock.patch.object(
        runner, "_validation_worktree_path", return_value=Path("/tmp/unsafe-pr-validation")
    ), mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(
        runner, "run_command"
    ) as run:
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "reason=validation_worktree_path_unsafe" in report
    run.assert_not_called()


def test_validate_pr_branch_uses_exact_pr_head_selection(tmp_path: Path) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"
    exists = {validation_path: False}

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command == [
            "gh",
            "pr",
            "view",
            "123",
            "--repo",
            runner.REPO,
            "--json",
            "number,state,baseRefName,headRefName,headRefOid",
        ]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            exists[validation_path] = True
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 0, "99 passed\n"
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True) as path_exists, mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        path_exists.side_effect = lambda path: exists.get(path, False)
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert f"head_sha={HEAD_SHA}" in report
    assert [
        "git",
        "fetch",
        "origin",
        "+refs/pull/123/head:refs/remotes/origin/pr-validation/123",
    ] in commands
    assert [
        "git",
        "worktree",
        "add",
        "--detach",
        str(validation_path),
        HEAD_SHA,
    ] in commands
    assert ["python3", "-m", "pytest", "-q"] in commands


def test_validate_pr_branch_knowledge_intake_profile_runs_allowlisted_tests(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command in (
            ["python3", "-m", "pytest", "-q", "tests/test_knowledge_intake.py"],
            ["python3", "-m", "pytest", "-q"],
        ) and cwd == validation_path:
            return 0, ""
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        report = runner.validate_pr_branch(
            _validate_pr_issue_body(profile="knowledge_intake")
        )

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert commands[-2:] == [
        ["python3", "-m", "pytest", "-q", "tests/test_knowledge_intake.py"],
        ["python3", "-m", "pytest", "-q"],
    ]
    assert "failed_output_start" not in report


def test_validate_pr_branch_failed_knowledge_intake_command_reports_output(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"
    pytest_output = "\n".join(
        (
            "tests/test_knowledge_intake.py::test_rejects_unknown_entry FAILED",
            "E       AssertionError: expected unknown entry to be rejected",
            "SKELETON_TG_CALLBACK_HMAC_SECRET=should-not-leak",
            "1 failed, 4 passed",
        )
    )

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if (
            command
            == ["python3", "-m", "pytest", "-q", "tests/test_knowledge_intake.py"]
            and cwd == validation_path
        ):
            return 1, pytest_output
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ):
        report = runner.validate_pr_branch(
            _validate_pr_issue_body(profile="knowledge_intake")
        )

    assert report.startswith("BLOCKED:")
    assert "step=validation_profile_command_1 status=failed exit_code=1" in report
    assert (
        "failed_command=python3 -m pytest -q tests/test_knowledge_intake.py"
        in report
    )
    assert "failed_output_start" in report
    assert "AssertionError: expected unknown entry to be rejected" in report
    assert "SKELETON_TG_CALLBACK_HMAC_SECRET=should-not-leak" not in report
    assert "[redacted environment variable]" in report
    assert "failed_output_end" in report


def test_validate_pr_branch_failed_command_output_is_truncated(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"
    long_output = "pytest failure line\n" + ("x" * 5000)

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 1, long_output
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ):
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    output_block = report.split("failed_output_start\n", 1)[1].split(
        "\nfailed_output_end", 1
    )[0]
    assert report.startswith("BLOCKED:")
    assert "pytest failure line" in output_block
    assert runner.VALIDATION_FAILED_OUTPUT_TRUNCATED_MARKER in output_block
    assert len(output_block) <= runner.VALIDATION_FAILED_OUTPUT_LIMIT


def test_validate_pr_branch_issue_body_does_not_execute_arbitrary_commands(
    tmp_path: Path,
) -> None:
    issue = _maintenance_issue(
        runner.VALIDATE_PR_BRANCH,
        "sudo env\n"
        "git push\n"
        "gh pr merge 123\n"
        "codex exec unsafe\n"
        "python3 -c 'print(1)'",
        metadata="\n".join(
            (
                "Pull Request: 123",
                f"Expected Head SHA: {HEAD_SHA}",
                "Validation Profile: full_pytest",
            )
        ),
    )
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 0, ""
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=False), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue, workdir=str(runner.ROOT))

    command_words = [" ".join(call.args[0]) for call in run.call_args_list]
    assert all("sudo" not in command for command in command_words)
    assert all("env" not in command for command in command_words)
    assert all("push" not in command for command in command_words)
    assert all("merge" not in command for command in command_words)
    assert all("codex" not in command for command in command_words)
    assert all("-c" not in command for command in command_words)
    run_codex.assert_not_called()


def test_inspect_pr_mergeability_missing_pr_number_blocks() -> None:
    report = runner.inspect_pr_mergeability(_inspect_pr_issue_body(pr_number=None))

    assert report.startswith("BLOCKED:")
    assert "reason=missing_or_invalid_pull_request" in report


def test_inspect_pr_mergeability_unsupported_repository_blocks() -> None:
    report = runner.inspect_pr_mergeability(
        _inspect_pr_issue_body(repository="alanua/Other")
    )

    assert report.startswith("BLOCKED:")
    assert "reason=unsupported_repository" in report


def test_inspect_pr_mergeability_expected_head_sha_mismatch_blocks() -> None:
    with mock.patch.object(
        runner, "_get_pr_mergeability_state", return_value=_inspect_pr_state()
    ):
        report = runner.inspect_pr_mergeability(
            _inspect_pr_issue_body(expected_head_sha="b" * 40)
        )

    assert report.startswith("BLOCKED:")
    assert "head_sha=" + HEAD_SHA in report
    assert "reason=expected_head_sha_mismatch" in report
    assert "next_action=refresh_inspection_request" in report


def test_inspect_pr_mergeability_closed_pr_reports_obsolete() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_mergeability_state",
        return_value=_inspect_pr_state(pr={"state": "closed"}),
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "pr_state=closed" in report
    assert "reason=pr_not_open" in report
    assert "next_action=obsolete_close_or_reopen_request" in report


def test_inspect_pr_mergeability_draft_pr_reports_draft_next_action() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_mergeability_state",
        return_value=_inspect_pr_state(pr={"draft": True}),
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "draft=true" in report
    assert "reason=pr_is_draft" in report
    assert "next_action=mark_pr_ready_for_review" in report


def test_inspect_pr_mergeability_validation_missing_reports_next_action() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_mergeability_state",
        return_value=_inspect_pr_state(
            combined_status={"state": "pending", "statuses": []},
            check_runs=[],
        ),
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "validation_state=missing" in report
    assert "reason=validation_missing" in report
    assert "next_action=run_required_validation" in report


def test_inspect_pr_mergeability_open_mergeable_pr_reports_ready_next_action() -> None:
    with mock.patch.object(
        runner, "_get_pr_mergeability_state", return_value=_inspect_pr_state()
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("DONE:")
    assert f"repository={runner.REPO}" in report
    assert "pr_state=open" in report
    assert "draft=false" in report
    assert "base_branch=main" in report
    assert f"head_sha={HEAD_SHA}" in report
    assert "mergeable=true" in report
    assert "changed_files=scripts/runner_poll_github_tasks.py" in report
    assert "ahead_by=1" in report
    assert "behind_by=0" in report
    assert "next_action=mark_ready_or_merge" in report


def test_inspect_pr_mergeability_diverged_pr_reports_refresh_next_action() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_mergeability_state",
        return_value=_inspect_pr_state(
            compare={"status": "diverged", "ahead_by": 2, "behind_by": 1}
        ),
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "compare_status=diverged" in report
    assert "reason=branch_behind_or_diverged" in report
    assert "next_action=refresh_pr_branch" in report


def test_inspect_pr_mergeability_non_mergeable_pr_reports_conflict_next_action() -> None:
    with mock.patch.object(
        runner,
        "_get_pr_mergeability_state",
        return_value=_inspect_pr_state(
            pr={"mergeable": False, "mergeable_state": "dirty"}
        ),
    ):
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("BLOCKED:")
    assert "mergeable=false" in report
    assert "reason=pr_has_merge_conflicts" in report
    assert "next_action=resolve_merge_conflicts" in report


def test_inspect_pr_mergeability_uses_github_api_only() -> None:
    payloads = {
        f"https://api.github.com/repos/{runner.REPO}/pulls/123": _inspect_pr_state()[
            "pr"
        ],
        (
            f"https://api.github.com/repos/{runner.REPO}/pulls/123/files"
            "?per_page=100&page=1"
        ): _inspect_pr_state()["files"],
        (
            f"https://api.github.com/repos/{runner.REPO}/compare/"
            f"{'b' * 40}...{HEAD_SHA}"
        ): _inspect_pr_state()["compare"],
        f"https://api.github.com/repos/{runner.REPO}/commits/{HEAD_SHA}/status": _inspect_pr_state()[
            "combined_status"
        ],
        (
            f"https://api.github.com/repos/{runner.REPO}/commits/{HEAD_SHA}/check-runs"
            "?per_page=100&page=1"
        ): {"check_runs": []},
    }

    def urlopen(request: object, timeout: int = 0) -> mock.MagicMock:
        del timeout
        assert isinstance(request, runner.urllib.request.Request)
        return _json_response(payloads[request.full_url])

    with mock.patch.object(runner.urllib.request, "urlopen", side_effect=urlopen), mock.patch.object(
        runner, "run_command"
    ) as run:
        report = runner.inspect_pr_mergeability(_inspect_pr_issue_body())

    assert report.startswith("DONE:")
    run.assert_not_called()


def test_inspect_pr_mergeability_issue_body_does_not_execute_arbitrary_commands() -> None:
    issue = _maintenance_issue(
        runner.INSPECT_PR_MERGEABILITY,
        "sudo env\n"
        "git push\n"
        "gh pr merge 123\n"
        "codex exec unsafe\n"
        "python3 -c 'print(1)'",
        metadata="\n".join(
            (
                f"Repository: {runner.REPO}",
                "Pull Request: 123",
                f"Expected Head SHA: {HEAD_SHA}",
            )
        ),
    )
    with mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(runner, "set_issue_label"), mock.patch.object(
        runner, "post_issue_comment"
    ), mock.patch.object(
        runner, "notify_task_finished"
    ), mock.patch.object(
        runner, "_get_pr_mergeability_state", return_value=_inspect_pr_state()
    ), mock.patch.object(
        runner, "run_command"
    ) as run, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(issue, workdir=str(runner.ROOT))

    run.assert_not_called()
    run_codex.assert_not_called()


def test_validate_pr_branch_removes_existing_validation_worktree_only(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validate-pr-branch" / "pr-123"

    def run_validation_command(
        command: list[str], cwd: str | Path | None = None
    ) -> tuple[int, str]:
        if command[:3] == ["gh", "pr", "view"]:
            return 0, json.dumps(_pr_validation_state())
        if command[:4] == ["git", "worktree", "remove", "--force"]:
            return 0, ""
        if command[:3] == ["git", "fetch", "origin"]:
            return 0, ""
        if command[:2] == ["git", "rev-parse"] and cwd == runner.ROOT:
            return 0, f"{HEAD_SHA}\n"
        if command[:3] == ["git", "worktree", "add"]:
            return 0, ""
        if command == ["git", "rev-parse", "HEAD"] and cwd == validation_path:
            return 0, f"{HEAD_SHA}\n"
        if command == ["python3", "-m", "pytest", "-q"] and cwd == validation_path:
            return 0, ""
        return 2, "unexpected command"

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(tmp_path)}, clear=True
    ), mock.patch.object(Path, "exists", autospec=True, return_value=True), mock.patch.object(
        Path, "mkdir", autospec=True
    ), mock.patch.object(
        runner, "run_command", side_effect=run_validation_command
    ) as run:
        report = runner.validate_pr_branch(_validate_pr_issue_body())

    commands = [call.args[0] for call in run.call_args_list]
    assert report.startswith("DONE:")
    assert [
        "git",
        "worktree",
        "remove",
        "--force",
        str(validation_path),
    ] in commands
    assert all(command[:2] != ["git", "commit"] for command in commands)
    assert all(command[:2] != ["git", "push"] for command in commands)
    assert all(command[:3] != ["gh", "pr", "merge"] for command in commands)


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
