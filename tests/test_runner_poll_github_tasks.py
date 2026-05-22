from __future__ import annotations

import json
import os
import urllib.parse
from unittest import mock

from scripts import runner_poll_github_tasks as runner


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


def _telegram_response() -> mock.MagicMock:
    response = mock.MagicMock()
    response.__enter__.return_value = response
    return response


def _request_payload(urlopen: mock.MagicMock) -> dict[str, list[str]]:
    request = urlopen.call_args.args[0]
    return urllib.parse.parse_qs(request.data.decode("utf-8"))


def _plain_done_message(issue_number: int = 129) -> str:
    return runner.build_telegram_message(issue_number, "DONE", DONE_REPORT)


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
        "Завдання виконано.\n"
        "Підготовлено зміни для перевірки.\n"
        f"Репозиторій: {runner.REPO}\n"
        "PR: #123\n"
        "Рекомендація: спочатку переглянути в ChatGPT або відкрити PR.\n"
        "Ця кнопка нічого не деплоїть і не запускає на сервері."
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
    assert "Завдання виконано." in text
    assert "Підготовлено зміни для перевірки." in text
    assert "Рекомендація:" in text
    assert HEAD_SHA not in text
    assert "scripts/runner_poll_github_tasks.py" not in text
    assert "docs/TELEGRAM_APPROVAL_BUTTONS.md" not in text
    assert "Skeleton task completed" not in text
    assert "Recommended action" not in text


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
    assert "Завдання виконано." in str(card["text"])


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


def _maintenance_command_success(
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
        runner, "prepare_issue_branch"
    ) as prepare_branch, mock.patch.object(
        runner, "run_codex_task"
    ) as run_codex:
        runner.process_issue(
            _maintenance_issue(
                runner.SYNC_TELEGRAM_CALLBACK_POLLER_RUNTIME, "Task: use Codex"
            )
        )

    dispatch.assert_called_once()
    prepare_branch.assert_not_called()
    run_codex.assert_not_called()
    assert set_label.call_args_list == [
        mock.call(145, runner.LABEL_READY, runner.LABEL_RUNNING),
        mock.call(145, runner.LABEL_RUNNING, runner.LABEL_DONE),
    ]


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
        runner, "run_command", side_effect=_maintenance_command_success
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("DONE:")
    privileged_commands = [
        call.args[0] for call in run.call_args_list if call.args[0][0] == "sudo"
    ]
    assert privileged_commands
    assert all(command[:2] == ["sudo", "-n"] for command in privileged_commands)


def test_sync_task_uses_only_allowed_service_names() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_maintenance_command_success
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


def test_copied_maintenance_units_get_root_ownership_and_read_only_mode() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_maintenance_command_success
    ) as run:
        runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    commands = [call.args[0] for call in run.call_args_list]
    service_unit = f"/etc/systemd/system/{runner.TELEGRAM_CALLBACK_POLLER_SERVICE}"
    timer_unit = f"/etc/systemd/system/{runner.TELEGRAM_CALLBACK_POLLER_TIMER}"
    assert ["sudo", "-n", "chown", "root:root", service_unit, timer_unit] in commands
    assert ["sudo", "-n", "chmod", "0644", service_unit, timer_unit] in commands


def test_done_requires_callback_timer_active_verification() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_maintenance_command_success
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("DONE:")
    assert "step=verify_callback_timer_active status=done" in report
    assert [
        "sudo",
        "-n",
        "systemctl",
        "is-active",
        "--quiet",
        runner.TELEGRAM_CALLBACK_POLLER_TIMER,
    ] in [call.args[0] for call in run.call_args_list]


def test_done_requires_callback_service_success_verification() -> None:
    with mock.patch.object(
        runner, "run_command", side_effect=_maintenance_command_success
    ) as run:
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("DONE:")
    assert "step=verify_callback_service_result status=done" in report
    assert [
        "sudo",
        "-n",
        "systemctl",
        "show",
        "--property=Result",
        "--value",
        runner.TELEGRAM_CALLBACK_POLLER_SERVICE,
    ] in [call.args[0] for call in run.call_args_list]


def test_failed_callback_timer_verification_reports_blocked() -> None:
    def fail_timer_verification(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        if command[2:5] == ["systemctl", "is-active", "--quiet"]:
            return 3, ""
        return _maintenance_command_success(command, cwd)

    with mock.patch.object(runner, "run_command", side_effect=fail_timer_verification):
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("BLOCKED:")
    assert "step=verify_callback_timer_active status=failed exit_code=3" in report
    assert "success_criteria=not_met" in report


def test_failed_callback_service_verification_reports_blocked() -> None:
    def fail_service_verification(
        command: list[str], cwd: str | None = None
    ) -> tuple[int, str]:
        if command[:5] == ["sudo", "-n", "systemctl", "show", "--property=Result"]:
            return 0, "failed\n"
        return _maintenance_command_success(command, cwd)

    with mock.patch.object(runner, "run_command", side_effect=fail_service_verification):
        report = runner.sync_telegram_callback_poller_runtime(str(runner.ROOT))

    assert report.startswith("BLOCKED:")
    assert "step=verify_callback_service_result status=failed exit_code=0" in report
    assert "success_criteria=not_met" in report


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
        runner, "run_command", side_effect=_maintenance_command_success
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
