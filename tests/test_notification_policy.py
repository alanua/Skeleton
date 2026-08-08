from __future__ import annotations

from core.notification_policy import evaluate_notification_policy


def test_policy_suppresses_routine_statuses() -> None:
    for status in (
        "DONE",
        "BLOCKED",
        "RETRY",
        "WAITING_DEPENDENCY",
        "PROGRESS",
        "SCHEDULED_WAKE",
        "PROVIDER_FALLBACK",
        "RECOVERY_DONE",
    ):
        decision = evaluate_notification_policy(
            source="runner_task_finished",
            issue_number=2227,
            status=status,
        )

        assert decision.should_emit is False
        assert decision.reason == "routine_status_suppressed"


def test_policy_allows_true_needs_operator_once() -> None:
    first = evaluate_notification_policy(
        source="runner_task_finished",
        issue_number=2227,
        status="NEEDS_OPERATOR",
        report="NEEDS_OPERATOR: retry policy blocked repeated execution.",
    )
    duplicate = evaluate_notification_policy(
        source="runner_task_finished",
        issue_number=2227,
        status="NEEDS_OPERATOR",
        report="NEEDS_OPERATOR: retry policy blocked repeated execution.",
        emitted_idempotency_keys={first.idempotency_key},
    )

    assert first.should_emit is True
    assert first.reason == "operator_exception_allowed"
    assert duplicate.should_emit is False
    assert duplicate.reason == "duplicate_notification_suppressed"
