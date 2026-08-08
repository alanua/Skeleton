from __future__ import annotations

import hashlib


NEEDS_OPERATOR_STATUS = "NEEDS_OPERATOR"
NOTIFICATION_LEDGER_LIMIT = 1000


def should_emit_task_notification(status: str) -> bool:
    return status == NEEDS_OPERATOR_STATUS


def notification_ledger_key(issue_number: int, status: str) -> str:
    if issue_number <= 0:
        raise ValueError("issue_number must be positive")
    if not should_emit_task_notification(status):
        raise ValueError("status is not operator-notifiable")
    return f"runner-task:{issue_number}:{status}"


def notification_report_hash(report: str | None) -> str:
    value = "" if report is None else report
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
