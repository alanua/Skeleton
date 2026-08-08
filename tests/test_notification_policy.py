from __future__ import annotations

from core.notification_policy import (
    NOTIFICATION_LEDGER_LIMIT,
    notification_ledger_key,
    notification_report_hash,
    should_emit_task_notification,
)
from core.scheduler_store import SchedulerStore


def test_routine_statuses_are_suppressed() -> None:
    assert should_emit_task_notification("NEEDS_OPERATOR") is True
    assert should_emit_task_notification("DONE") is False
    assert should_emit_task_notification("BLOCKED") is False
    assert should_emit_task_notification("retry") is False


def test_notification_hash_is_stable_and_public_safe() -> None:
    digest = notification_report_hash("NEEDS_OPERATOR: approve retry")

    assert len(digest) == 64
    assert digest == notification_report_hash("NEEDS_OPERATOR: approve retry")
    assert digest != notification_report_hash("NEEDS_OPERATOR: different")


def test_notification_ledger_claim_is_restart_safe_and_bounded(tmp_path) -> None:
    db_path = tmp_path / "scheduler.sqlite3"
    first_store = SchedulerStore(db_path)
    first_store.initialize()

    key = notification_ledger_key(2243, "NEEDS_OPERATOR")
    report_hash = notification_report_hash("NEEDS_OPERATOR: approve retry")
    assert first_store.claim_notification_once(
        notification_key=key,
        issue_number=2243,
        status="NEEDS_OPERATOR",
        report_hash=report_hash,
        now=100,
        limit=NOTIFICATION_LEDGER_LIMIT,
    ) is True

    restarted_store = SchedulerStore(db_path)
    restarted_store.initialize()
    assert restarted_store.claim_notification_once(
        notification_key=key,
        issue_number=2243,
        status="NEEDS_OPERATOR",
        report_hash=report_hash,
        now=101,
        limit=NOTIFICATION_LEDGER_LIMIT,
    ) is False

    assert restarted_store.notification_ledger_count() == 1


def test_notification_ledger_prunes_old_entries(tmp_path) -> None:
    store = SchedulerStore(tmp_path / "scheduler.sqlite3")
    store.initialize()

    for issue_number in range(1, 5):
        assert store.claim_notification_once(
            notification_key=notification_ledger_key(issue_number, "NEEDS_OPERATOR"),
            issue_number=issue_number,
            status="NEEDS_OPERATOR",
            report_hash=notification_report_hash(str(issue_number)),
            now=issue_number,
            limit=2,
        ) is True

    assert store.notification_ledger_count() == 2
