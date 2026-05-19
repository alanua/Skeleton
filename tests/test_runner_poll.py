from __future__ import annotations

from pathlib import Path
from unittest import mock

from scripts import runner_poll_github_tasks as runner


ROOT = Path(__file__).resolve().parents[1]


def test_extract_task_block_found() -> None:
    fence = "`" * 3
    body = f"before\n{fence}task\nDo the thing.\n{fence}\nafter"

    assert runner.extract_task_block(body) == "Do the thing."


def test_extract_task_block_not_found() -> None:
    assert runner.extract_task_block("No task fence here.") is None


def test_get_ready_issues_calls_gh_cli() -> None:
    with mock.patch.object(
        runner, "run_command", return_value=(0, '[{"number": 1, "title": "T", "body": "B"}]')
    ) as run_command:
        issues = runner.get_ready_issues()

    assert issues == [{"number": 1, "title": "T", "body": "B"}]
    run_command.assert_called_once_with(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            runner.REPO,
            "--label",
            runner.LABEL_READY,
            "--state",
            "open",
            "--json",
            "number,title,body",
        ]
    )


def test_post_issue_comment_calls_gh_cli() -> None:
    with mock.patch.object(runner, "run_command", return_value=(0, "")) as run_command:
        runner.post_issue_comment(7, "done")

    run_command.assert_called_once_with(
        [
            "gh",
            "issue",
            "comment",
            "7",
            "--repo",
            runner.REPO,
            "--body",
            "done",
        ]
    )


def test_set_issue_label_calls_gh_cli() -> None:
    with mock.patch.object(runner, "run_command", return_value=(0, "")) as run_command:
        runner.set_issue_label(7, runner.LABEL_READY, runner.LABEL_DONE)

    run_command.assert_called_once_with(
        [
            "gh",
            "issue",
            "edit",
            "7",
            "--repo",
            runner.REPO,
            "--remove-label",
            runner.LABEL_READY,
            "--add-label",
            runner.LABEL_DONE,
        ]
    )


def test_run_codex_task_calls_codex_exec() -> None:
    with mock.patch.object(runner, "run_command", return_value=(0, "ok")) as run_command:
        code, output = runner.run_codex_task("Task body", "/repo")

    assert (code, output) == (0, "ok")
    run_command.assert_called_once_with(
        [
            "codex",
            "exec",
            "--sandbox",
            "workspace-write",
            "--cd",
            "/repo",
            "Task body",
        ],
        cwd="/repo",
    )


def test_process_issue_blocks_when_no_task_block() -> None:
    issue = {"number": 3, "title": "Missing", "body": "plain text"}

    with mock.patch.object(runner, "post_issue_comment") as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label:
        runner.process_issue(issue, workdir="/repo")

    comment.assert_called_once()
    assert comment.call_args.args[0] == 3
    assert "BLOCKED" in comment.call_args.args[1]
    label.assert_called_once_with(3, runner.LABEL_READY, runner.LABEL_BLOCKED)


def test_process_issue_posts_blocked_on_codex_failure() -> None:
    fence = "`" * 3
    issue = {"number": 4, "title": "Fail", "body": f"{fence}task\nDo it\n{fence}"}

    with mock.patch.object(runner, "ensure_clean_worktree", return_value=(True, "")), mock.patch.object(
        runner, "prepare_issue_branch", return_value=(0, "", "runner/issue-4")
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(1, "codex failed")
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label:
        runner.process_issue(issue, workdir="/repo")

    comment.assert_called_once()
    assert "BLOCKED" in comment.call_args.args[1]
    assert "codex failed" in comment.call_args.args[1]
    label.assert_called_once_with(4, runner.LABEL_READY, runner.LABEL_BLOCKED)


def test_process_issue_posts_done_on_success() -> None:
    fence = "`" * 3
    issue = {"number": 5, "title": "Success", "body": f"{fence}task\nDo it\n{fence}"}

    with mock.patch.object(runner, "ensure_clean_worktree", return_value=(True, "")), mock.patch.object(
        runner, "prepare_issue_branch", return_value=(0, "", "runner/issue-5")
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex ok")
    ), mock.patch.object(
        runner, "finalize_success", return_value="DONE report"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label:
        runner.process_issue(issue, workdir="/repo")

    comment.assert_called_once_with(5, "DONE report")
    label.assert_called_once_with(5, runner.LABEL_READY, runner.LABEL_DONE)


def test_poll_once_processes_ready_issues() -> None:
    issues = [
        {"number": 1, "title": "One", "body": ""},
        {"number": 2, "title": "Two", "body": ""},
    ]

    with mock.patch.object(runner, "get_ready_issues", return_value=issues), mock.patch.object(
        runner, "process_issue"
    ) as process_issue:
        processed = runner.poll_once(workdir="/repo")

    assert processed == 2
    assert process_issue.call_count == 2


def test_cleanup_runtime_artifacts_removes_known_generated_directories(tmp_path: Path) -> None:
    for relative_path in runner.RUNTIME_ARTIFACTS:
        artifact = tmp_path / relative_path
        artifact.mkdir(parents=True)
        (artifact / "generated.txt").write_text("generated", encoding="utf-8")

    runner.cleanup_runtime_artifacts(tmp_path)

    for relative_path in runner.RUNTIME_ARTIFACTS:
        assert not (tmp_path / relative_path).exists()


def test_service_uses_agent_user() -> None:
    service = (ROOT / "scripts" / "skeleton-runner-poll.service").read_text(
        encoding="utf-8"
    )

    assert "User=agent" in service
    assert "ExecStart=/usr/bin/python3 scripts/runner_poll_github_tasks.py" in service


def test_timer_runs_every_60_seconds() -> None:
    timer = (ROOT / "scripts" / "skeleton-runner-poll.timer").read_text(
        encoding="utf-8"
    )

    assert "OnUnitActiveSec=60" in timer


def test_no_hardcoded_token_patterns() -> None:
    paths = [
        ROOT / "scripts" / "runner_poll_github_tasks.py",
        ROOT / "scripts" / "README_RUNNER_SETUP.md",
        ROOT / "tests" / "test_runner_poll.py",
    ]
    blocked_patterns = (
        "gh" + "p_",
        "github" + "_pat_",
        "s" + "k-",
        "AK" + "IA",
        "xox" + "b-",
    )

    for path in paths:
        content = path.read_text(encoding="utf-8")
        for pattern in blocked_patterns:
            assert pattern not in content
