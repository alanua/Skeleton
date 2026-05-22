from __future__ import annotations

from pathlib import Path
from unittest import mock

from scripts import runner_poll_github_tasks as runner


def _issue(number: int) -> dict[str, object]:
    return {
        "number": number,
        "title": "Issue workspace lifecycle",
        "body": "```task\nDo it\n```",
    }


def test_successful_issue_cleans_issue_workspace_before_marking_done(
    tmp_path: Path,
) -> None:
    issue_path = tmp_path / "worktrees" / "issue-161"

    with mock.patch.object(
        runner, "prepare_issue_worktree", return_value=(0, "ready", issue_path)
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex ok")
    ), mock.patch.object(
        runner, "finalize_success", return_value="DONE report"
    ), mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "cleanup_issue_worktree", return_value=(0, "")
    ) as cleanup_worktree, mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label, mock.patch.object(runner, "notify_task_finished"):
        runner.process_issue(_issue(161), workdir="/coordinator")

    cleanup_worktree.assert_called_once_with(161, "/coordinator")
    comment.assert_called_once_with(161, "DONE report")
    assert label.call_args_list == [
        mock.call(161, runner.LABEL_READY, runner.LABEL_RUNNING),
        mock.call(161, runner.LABEL_RUNNING, runner.LABEL_DONE),
    ]


def test_failed_issue_keeps_workspace_path_for_review(tmp_path: Path) -> None:
    issue_path = tmp_path / "worktrees" / "issue-162"

    with mock.patch.object(
        runner, "prepare_issue_worktree", return_value=(0, "ready", issue_path)
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(1, "codex failed")
    ), mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "cleanup_issue_worktree"
    ) as cleanup_worktree, mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(runner, "notify_task_finished"):
        runner.process_issue(_issue(162), workdir="/coordinator")

    cleanup_worktree.assert_not_called()
    assert "codex failed" in comment.call_args.args[1]
    assert "Issue workspace kept for review" in comment.call_args.args[1]
    assert str(issue_path) in comment.call_args.args[1]


def test_cleanup_failure_blocks_with_retained_workspace_path(tmp_path: Path) -> None:
    issue_path = tmp_path / "worktrees" / "issue-163"

    with mock.patch.object(
        runner, "prepare_issue_worktree", return_value=(0, "ready", issue_path)
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex ok")
    ), mock.patch.object(
        runner, "finalize_success", return_value="DONE report"
    ), mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ), mock.patch.object(
        runner, "cleanup_issue_worktree", return_value=(1, "still dirty")
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label, mock.patch.object(runner, "notify_task_finished"):
        runner.process_issue(_issue(163), workdir="/coordinator")

    assert "Issue workspace cleanup failed" in comment.call_args.args[1]
    assert "Issue workspace kept for review" in comment.call_args.args[1]
    assert str(issue_path) in comment.call_args.args[1]
    assert label.call_args_list == [
        mock.call(163, runner.LABEL_READY, runner.LABEL_RUNNING),
        mock.call(163, runner.LABEL_RUNNING, runner.LABEL_BLOCKED),
    ]
