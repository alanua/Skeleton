from __future__ import annotations

from core.notification_policy import should_notify_operator_for_runner_result


def test_routine_awaiting_approval_prompt_does_not_notify_operator() -> None:
    report = (
        "DONE: Codex completed successfully.\n"
        "Draft PR: https://github.com/alanua/Skeleton/pull/123\n"
        "Коментар: Перевір у ChatGPT перед схваленням."
    )

    assert should_notify_operator_for_runner_result("DONE", report) is False


def test_internal_review_request_changes_does_not_notify_operator() -> None:
    report = "internal_review_verdict=REQUEST_CHANGES\nrepair_task_id=repair-pr-1"

    assert should_notify_operator_for_runner_result("BLOCKED", report) is False


def test_true_needs_operator_still_notifies() -> None:
    assert should_notify_operator_for_runner_result(
        "NEEDS_OPERATOR",
        "internal_review_verdict=NEEDS_OPERATOR\nreason=security_or_secret_boundary",
    ) is True
