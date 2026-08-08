from __future__ import annotations

from core.notification_policy import (
    claim_operator_notification,
    should_notify_operator_status,
)
from core.scheduler_store import SchedulerStore


def test_plain_routine_statuses_do_not_notify_operator() -> None:
    for status in ("DONE", "BLOCKED", "RETRY", "WAITING", "REVIEW"):
        assert should_notify_operator_status(status) is False


def test_true_needs_operator_notifies_once_across_store_reopen(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    first = SchedulerStore(db_path)
    assert claim_operator_notification(
        first,
        status="NEEDS_OPERATOR",
        now=100,
        issue_number=2294,
        repository="alanua/Skeleton",
        pr_number=2294,
        head_sha="a" * 40,
        reason="protected_merge_requires_operator",
        payload={"next_step": "operator_review_required"},
    ) is True

    reopened = SchedulerStore(db_path)
    assert claim_operator_notification(
        reopened,
        status="NEEDS_OPERATOR",
        now=101,
        issue_number=2294,
        repository="alanua/Skeleton",
        pr_number=2294,
        head_sha="a" * 40,
        reason="protected_merge_requires_operator",
        payload={"next_step": "operator_review_required"},
    ) is False
