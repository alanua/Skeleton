from __future__ import annotations

import os
import urllib.parse
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
        runner,
        "run_command",
        return_value=(
            0,
            '[{"number": 1, "title": "T", "body": "B", "state": "OPEN", '
            '"closed": false, "url": "https://github.com/alanua/Skeleton/issues/1"}]',
        ),
    ) as run_command:
        issues = runner.get_ready_issues()

    assert issues == [
        {
            "number": 1,
            "title": "T",
            "body": "B",
            "state": "OPEN",
            "closed": False,
            "url": "https://github.com/alanua/Skeleton/issues/1",
        }
    ]
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
            "--search",
            "is:issue",
            "--json",
            "number,title,body,state,url,closed",
        ]
    )


def test_get_ready_issues_filters_stale_and_pull_request_items() -> None:
    output = """
    [
      {
        "number": 1,
        "title": "Issue",
        "body": "B",
        "state": "OPEN",
        "closed": false,
        "url": "https://github.com/alanua/Skeleton/issues/1"
      },
      {
        "number": 2,
        "title": "Closed",
        "body": "B",
        "state": "CLOSED",
        "closed": true,
        "url": "https://github.com/alanua/Skeleton/issues/2"
      },
      {
        "number": 3,
        "title": "PR",
        "body": "B",
        "state": "OPEN",
        "closed": false,
        "url": "https://github.com/alanua/Skeleton/pull/3"
      }
    ]
    """

    with mock.patch.object(runner, "run_command", return_value=(0, output)):
        issues = runner.get_ready_issues()

    assert [issue["number"] for issue in issues] == [1]


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


def test_apply_runner_lane_label_calls_gh_cli_for_explicit_lane_metadata() -> None:
    task = runner.RunnerTask(
        content="Do it",
        lane=runner.RunnerLane("lane-2"),
        has_lane_metadata=True,
    )

    with mock.patch.object(runner, "run_command", return_value=(0, "")) as run_command:
        runner.apply_runner_lane_label(7, task)

    run_command.assert_called_once_with(
        [
            "gh",
            "issue",
            "edit",
            "7",
            "--repo",
            runner.REPO,
            "--add-label",
            runner.RUNNER_LANE_LABELS["lane-2"],
        ]
    )


def test_apply_runner_lane_label_skips_implicit_default_lane() -> None:
    with mock.patch.object(runner, "run_command") as run_command:
        runner.apply_runner_lane_label(7, runner.RunnerTask(content="Do it"))

    run_command.assert_not_called()


def test_send_telegram_notification_skips_when_env_missing() -> None:
    with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
        runner.urllib.request, "urlopen"
    ) as urlopen:
        runner.send_telegram_notification("done")

    urlopen.assert_not_called()


def test_send_telegram_notification_posts_with_env() -> None:
    response = mock.MagicMock()
    response.__enter__.return_value = response
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
        runner.send_telegram_notification("Repository: alanua/Skeleton\nStatus: DONE")

    request = urlopen.call_args.args[0]
    assert request.full_url == (
        f"{runner.TELEGRAM_API_BASE}/bottelegram-bot-placeholder/sendMessage"
    )
    assert urlopen.call_args.kwargs == {"timeout": runner.TELEGRAM_TIMEOUT_SECONDS}
    payload = urllib.parse.parse_qs(request.data.decode("utf-8"))
    assert payload == {
        "chat_id": ["telegram-chat-placeholder"],
        "text": ["Repository: alanua/Skeleton\nStatus: DONE"],
        "disable_web_page_preview": ["true"],
    }


def test_notify_task_finished_swallows_telegram_send_failure() -> None:
    with mock.patch.object(
        runner, "should_notify_task_finished", return_value=True
    ), mock.patch.object(
        runner, "send_telegram_notification", side_effect=RuntimeError("send failed")
    ):
        runner.notify_task_finished(8, "DONE", "DONE report")


def test_notify_task_finished_closed_pull_request_does_not_notify() -> None:
    fence = "`" * 3
    issue = {
        "number": 4,
        "body": f"{fence}task\nDo it\n{fence}",
        "state": "CLOSED",
        "closed": True,
        "url": "https://github.com/alanua/Skeleton/pull/4",
        "labels": [{"name": runner.LABEL_BLOCKED}],
    }

    with mock.patch.object(runner, "get_notification_issue", return_value=issue), mock.patch.object(
        runner, "send_telegram_notification"
    ) as send:
        runner.notify_task_finished(4, "BLOCKED")

    send.assert_not_called()


def test_notify_task_finished_closed_issue_does_not_notify() -> None:
    fence = "`" * 3
    issue = {
        "number": 6,
        "body": f"{fence}task\nDo it\n{fence}",
        "state": "CLOSED",
        "closed": True,
        "url": "https://github.com/alanua/Skeleton/issues/6",
        "labels": [{"name": runner.LABEL_BLOCKED}],
    }

    with mock.patch.object(runner, "get_notification_issue", return_value=issue), mock.patch.object(
        runner, "send_telegram_notification"
    ) as send:
        runner.notify_task_finished(6, "BLOCKED")

    send.assert_not_called()


def test_notify_task_finished_pull_request_metadata_does_not_notify() -> None:
    fence = "`" * 3
    issue = {
        "number": 11,
        "body": f"{fence}task\nDo it\n{fence}",
        "state": "OPEN",
        "closed": False,
        "url": "https://github.com/alanua/Skeleton/issues/11",
        "pull_request": {"url": "https://api.github.com/repos/alanua/Skeleton/pulls/11"},
        "labels": [{"name": runner.LABEL_DONE}],
    }

    with mock.patch.object(runner, "get_notification_issue", return_value=issue), mock.patch.object(
        runner, "send_telegram_notification"
    ) as send:
        runner.notify_task_finished(11, "DONE", "DONE report")

    send.assert_not_called()


def test_notify_task_finished_without_task_body_does_not_notify() -> None:
    issue = {
        "number": 12,
        "body": "No task fence here.",
        "state": "OPEN",
        "closed": False,
        "url": "https://github.com/alanua/Skeleton/issues/12",
        "labels": [{"name": runner.LABEL_DONE}],
    }

    with mock.patch.object(runner, "get_notification_issue", return_value=issue), mock.patch.object(
        runner, "send_telegram_notification"
    ) as send:
        runner.notify_task_finished(12, "DONE", "DONE report")

    send.assert_not_called()


def test_notify_task_finished_without_current_final_label_does_not_notify() -> None:
    fence = "`" * 3
    issue = {
        "number": 13,
        "body": f"{fence}task\nDo it\n{fence}",
        "state": "OPEN",
        "closed": False,
        "url": "https://github.com/alanua/Skeleton/issues/13",
        "labels": [{"name": runner.LABEL_RUNNING}],
    }

    with mock.patch.object(runner, "get_notification_issue", return_value=issue), mock.patch.object(
        runner, "send_telegram_notification"
    ) as send:
        runner.notify_task_finished(13, "DONE", "DONE report")

    send.assert_not_called()


def test_notify_task_finished_normal_runner_task_notifies() -> None:
    fence = "`" * 3
    issue = {
        "number": 14,
        "body": f"{fence}task\nDo it\n{fence}",
        "state": "OPEN",
        "closed": False,
        "url": "https://github.com/alanua/Skeleton/issues/14",
        "labels": [{"name": runner.LABEL_DONE}],
    }

    with mock.patch.object(runner, "get_notification_issue", return_value=issue), mock.patch.object(
        runner, "send_telegram_notification"
    ) as send:
        runner.notify_task_finished(14, "DONE", "DONE report")

    send.assert_called_once_with(
        f"Repository: {runner.REPO}\nIssue: #14\nStatus: DONE"
    )


def test_notify_task_finished_guard_failure_suppresses_notification_safely() -> None:
    with mock.patch.object(
        runner, "get_notification_issue", side_effect=RuntimeError("gh failed")
    ), mock.patch.object(runner, "send_telegram_notification") as send:
        runner.notify_task_finished(16, "DONE", "DONE report")

    send.assert_not_called()


def test_done_telegram_message_includes_pr_url_when_available() -> None:
    report = "DONE: ok\n\nDraft PR: https://github.example/pull/1"

    assert runner.build_telegram_message(9, "DONE", report) == (
        f"Repository: {runner.REPO}\n"
        "Issue: #9\n"
        "Status: DONE\n"
        "PR: https://github.example/pull/1"
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
            runner.build_codex_task_prompt("Task body", "/repo"),
        ],
        cwd="/repo",
    )


def test_codex_task_prompt_keeps_output_in_runner_issue_worktree() -> None:
    prompt = runner.build_codex_task_prompt("Task body", "/runner/issue-187")

    assert "/runner/issue-187" in prompt
    assert "Edit files only inside that issue worktree." in prompt
    assert "Do not create or use a separate clone, checkout, or worktree" in prompt
    assert prompt.endswith("Task body")


def test_process_issue_blocks_when_no_task_block() -> None:
    issue = {"number": 3, "title": "Missing", "body": "plain text"}

    with mock.patch.object(runner, "post_issue_comment") as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label, mock.patch.object(runner, "notify_task_finished") as notify:
        runner.process_issue(issue, workdir="/repo")

    comment.assert_called_once()
    assert comment.call_args.args[0] == 3
    assert "BLOCKED" in comment.call_args.args[1]
    label.assert_called_once_with(3, runner.LABEL_READY, runner.LABEL_BLOCKED)
    notify.assert_called_once_with(3, "BLOCKED")


def test_process_issue_silently_skips_pull_request_items() -> None:
    issue = {
        "number": 33,
        "title": "PR",
        "body": "plain text",
        "state": "OPEN",
        "closed": False,
        "url": "https://github.com/alanua/Skeleton/pull/33",
        "pull_request": {"url": "https://api.github.com/repos/alanua/Skeleton/pulls/33"},
    }

    with mock.patch.object(runner, "post_issue_comment") as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label, mock.patch.object(runner, "notify_task_finished") as notify, mock.patch.object(
        runner, "ensure_clean_worktree"
    ) as clean:
        runner.process_issue(issue, workdir="/repo")

    comment.assert_not_called()
    label.assert_not_called()
    notify.assert_not_called()
    clean.assert_not_called()


def test_process_issue_silently_skips_closed_items() -> None:
    issue = {
        "number": 34,
        "title": "Closed",
        "body": "plain text",
        "state": "CLOSED",
        "closed": True,
        "url": "https://github.com/alanua/Skeleton/issues/34",
    }

    with mock.patch.object(runner, "post_issue_comment") as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label, mock.patch.object(runner, "notify_task_finished") as notify, mock.patch.object(
        runner, "ensure_clean_worktree"
    ) as clean:
        runner.process_issue(issue, workdir="/repo")

    comment.assert_not_called()
    label.assert_not_called()
    notify.assert_not_called()
    clean.assert_not_called()


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
    assert label.call_args_list == [
        mock.call(4, runner.LABEL_READY, runner.LABEL_RUNNING),
        mock.call(4, runner.LABEL_RUNNING, runner.LABEL_BLOCKED),
    ]


def test_process_issue_posts_done_on_success() -> None:
    fence = "`" * 3
    issue = {"number": 5, "title": "Success", "body": f"{fence}task\nDo it\n{fence}"}

    with mock.patch.object(runner, "ensure_clean_worktree", return_value=(True, "")), mock.patch.object(
        runner, "prepare_issue_branch", return_value=(0, "", "runner/issue-5")
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex ok")
    ), mock.patch.object(
        runner,
        "finalize_success",
        return_value="DONE report\n\nDraft PR: https://github.example/pull/5",
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label, mock.patch.object(runner, "notify_task_finished") as notify:
        runner.process_issue(issue, workdir="/repo")

    report = "DONE report\n\nDraft PR: https://github.example/pull/5"
    comment.assert_called_once_with(5, report)
    assert label.call_args_list == [
        mock.call(5, runner.LABEL_READY, runner.LABEL_RUNNING),
        mock.call(5, runner.LABEL_RUNNING, runner.LABEL_DONE),
    ]
    notify.assert_called_once_with(5, "DONE", report)


def test_process_issue_reports_runner_lane_metadata_on_success() -> None:
    issue = {
        "number": 25,
        "title": "Lane report",
        "body": "Runner Lane: lane-2\n\n```task\nDo it\n```",
    }

    with mock.patch.object(
        runner, "prepare_issue_branch", return_value=(0, "", "runner/issue-25")
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex ok")
    ), mock.patch.object(
        runner, "finalize_success", return_value="DONE report"
    ), mock.patch.object(
        runner, "cleanup_issue_worktree", return_value=(0, "")
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(
        runner, "apply_runner_lane_label"
    ) as lane_label, mock.patch.object(runner, "notify_task_finished"):
        runner.process_issue(issue, workdir="/repo")

    lane_label.assert_called_once_with(
        25,
        runner.RunnerTask(
            content="Do it",
            lane=runner.RunnerLane("lane-2"),
            has_lane_metadata=True,
        ),
    )
    comment.assert_called_once_with(25, "DONE report\nRunner Lane: lane-2")


def test_block_issue_reports_runner_lane_metadata() -> None:
    task = runner.RunnerTask(
        content="Do it",
        lane=runner.RunnerLane("lane-1"),
        has_lane_metadata=True,
    )

    with mock.patch.object(runner, "post_issue_comment") as comment, mock.patch.object(
        runner, "set_issue_label"
    ), mock.patch.object(runner, "notify_task_finished"):
        runner.block_issue(26, "Codex task failed.", runner_task=task)

    comment.assert_called_once_with(
        26,
        "BLOCKED: Codex task failed.\nRunner Lane: lane-1",
    )


def test_process_issue_keeps_done_transition_when_telegram_send_fails() -> None:
    fence = "`" * 3
    issue = {"number": 15, "title": "Notify fail", "body": f"{fence}task\nDo it\n{fence}"}

    with mock.patch.dict(
        os.environ,
        {
            "SKELETON_TG_BOT": "telegram-bot-placeholder",
            "SKELETON_TG_CHAT": "telegram-chat-placeholder",
        },
        clear=True,
    ), mock.patch.object(
        runner, "ensure_clean_worktree", return_value=(True, "")
    ), mock.patch.object(
        runner, "prepare_issue_branch", return_value=(0, "", "runner/issue-15")
    ), mock.patch.object(
        runner, "run_codex_task", return_value=(0, "codex ok")
    ), mock.patch.object(
        runner, "finalize_success", return_value="DONE report"
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label, mock.patch.object(
        runner.urllib.request, "urlopen", side_effect=RuntimeError("send failed")
    ):
        runner.process_issue(issue, workdir="/repo")

    comment.assert_called_once_with(15, "DONE report")
    assert label.call_args_list == [
        mock.call(15, runner.LABEL_READY, runner.LABEL_RUNNING),
        mock.call(15, runner.LABEL_RUNNING, runner.LABEL_DONE),
    ]


def test_process_issue_posts_blocked_on_branch_failure_after_claiming() -> None:
    fence = "`" * 3
    issue = {"number": 6, "title": "Branch fail", "body": f"{fence}task\nDo it\n{fence}"}

    with mock.patch.object(runner, "ensure_clean_worktree", return_value=(True, "")), mock.patch.object(
        runner, "prepare_issue_branch", return_value=(1, "branch failed", "runner/issue-6")
    ), mock.patch.object(
        runner, "post_issue_comment"
    ) as comment, mock.patch.object(
        runner, "set_issue_label"
    ) as label:
        runner.process_issue(issue, workdir="/repo")

    comment.assert_called_once()
    assert "BLOCKED" in comment.call_args.args[1]
    assert "branch failed" in comment.call_args.args[1]
    assert label.call_args_list == [
        mock.call(6, runner.LABEL_READY, runner.LABEL_RUNNING),
        mock.call(6, runner.LABEL_RUNNING, runner.LABEL_BLOCKED),
    ]


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
        if relative_path == ".codex":
            artifact.write_text("generated", encoding="utf-8")
            continue
        artifact.mkdir(parents=True)
        (artifact / "generated.txt").write_text("generated", encoding="utf-8")

    runner.cleanup_runtime_artifacts(tmp_path)

    for relative_path in runner.RUNTIME_ARTIFACTS:
        assert not (tmp_path / relative_path).exists()


def test_finalize_success_no_file_changes_cleans_runtime_artifacts_and_reports(
    tmp_path: Path,
) -> None:
    for relative_path in runner.RUNTIME_ARTIFACTS:
        artifact = tmp_path / relative_path
        if relative_path == ".codex":
            artifact.write_text("generated", encoding="utf-8")
            continue
        artifact.mkdir(parents=True)
        (artifact / "generated.txt").write_text("generated", encoding="utf-8")

    def run_command(args: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
        if args in (
            ["git", "diff", "--name-only"],
            ["git", "diff", "--cached", "--name-only"],
            ["git", "ls-files", "--others", "--exclude-standard"],
        ):
            return 0, ""
        raise AssertionError(f"unexpected command: {args}")

    issue = {"number": 24, "title": "No changes"}
    with mock.patch.object(runner, "run_command", side_effect=run_command):
        report = runner.finalize_success(issue, str(tmp_path), "codex ok")

    assert "DONE: Codex completed successfully with no file changes." in report
    assert "Runtime artifacts cleaned after Codex execution." in report
    assert "codex ok" in report
    for relative_path in runner.RUNTIME_ARTIFACTS:
        assert not (tmp_path / relative_path).exists()


def test_service_uses_agent_user() -> None:
    service = (ROOT / "scripts" / "skeleton-runner-poll.service").read_text(
        encoding="utf-8"
    )

    assert "User=agent" in service
    assert "EnvironmentFile=-/etc/skeleton-runner.env" in service
    assert "ExecStart=/usr/bin/python3 scripts/runner_poll_github_tasks.py" in service


def test_readme_documents_local_env_file_and_credential_rules() -> None:
    readme = (ROOT / "scripts" / "README_RUNNER_SETUP.md").read_text(
        encoding="utf-8"
    )

    assert "/etc/skeleton-runner.env" in readme
    assert "SKELETON_TG_BOT=replace-with-telegram-bot-token" in readme
    assert "SKELETON_TG_CHAT=replace-with-telegram-chat-id" in readme
    assert "chmod 600 /etc/skeleton-runner.env" in readme
    assert "must not be committed" in readme
    assert "task issues" in readme
    assert "sudo cp scripts/skeleton-runner-poll.service /etc/systemd/system/" in readme
    assert "sudo systemctl daemon-reload" in readme


def test_timer_runs_every_60_seconds() -> None:
    timer = (ROOT / "scripts" / "skeleton-runner-poll.timer").read_text(
        encoding="utf-8"
    )

    assert "OnUnitActiveSec=60" in timer


def test_no_hardcoded_token_patterns() -> None:
    paths = [
        ROOT / "scripts" / "runner_poll_github_tasks.py",
        ROOT / "scripts" / "skeleton-runner-poll.service",
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
