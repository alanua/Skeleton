from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from scripts import runner_poll_github_tasks as runner


def _issue(number: int) -> dict[str, object]:
    return {
        "number": number,
        "title": "Issue workspace lifecycle",
        "body": "```task\nDo it\n```",
    }


def _git(workdir: Path, *args: str) -> str:
    code, output = runner.run_command(["git", *args], cwd=workdir)
    assert code == 0, output
    return output


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


def test_cleanup_issue_worktree_force_removes_dirty_workspace_and_prunes_metadata(
    tmp_path: Path,
) -> None:
    worktree_root = tmp_path / "worktrees"
    issue_path = worktree_root / "issue-161"
    coordinator = tmp_path / "coordinator"
    issue_path.mkdir(parents=True)
    coordinator.mkdir()

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(worktree_root)}, clear=True
    ), mock.patch.object(
        runner, "cleanup_runtime_artifacts"
    ) as cleanup_artifacts, mock.patch.object(
        runner, "run_command", side_effect=((0, "removed"), (0, "pruned"))
    ) as run_command:
        code, output = runner.cleanup_issue_worktree(161, coordinator)

    assert code == 0
    assert "git worktree remove --force" in output
    assert "git worktree prune" in output
    cleanup_artifacts.assert_called_once_with(issue_path.resolve())
    assert run_command.call_args_list == [
        mock.call(
            ["git", "worktree", "remove", "--force", str(issue_path.resolve())],
            cwd=coordinator,
        ),
        mock.call(["git", "worktree", "prune"], cwd=coordinator),
    ]


def test_cleanup_issue_worktree_removes_dirty_workspace(tmp_path: Path) -> None:
    coordinator = tmp_path / "coordinator"
    worktree_root = tmp_path / "worktrees"
    issue_path = worktree_root / "issue-161"
    coordinator.mkdir()
    _git(coordinator, "init", "-b", "main")
    _git(coordinator, "config", "user.email", "runner@example.test")
    _git(coordinator, "config", "user.name", "Runner Test")
    (coordinator / "README.md").write_text("runner\n", encoding="utf-8")
    _git(coordinator, "add", "README.md")
    _git(coordinator, "commit", "-m", "initial")
    _git(
        coordinator,
        "worktree",
        "add",
        "-b",
        runner.issue_branch(161),
        str(issue_path),
        "HEAD",
    )
    (issue_path / "dirty.txt").write_text("keep until cleanup\n", encoding="utf-8")

    with mock.patch.dict(
        os.environ, {"SKELETON_WORKTREE_ROOT": str(worktree_root)}, clear=True
    ):
        code, output = runner.cleanup_issue_worktree(161, coordinator)

    assert code == 0, output
    assert not issue_path.exists()
    assert str(issue_path) not in _git(coordinator, "worktree", "list", "--porcelain")


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
