from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable


NOTIFICATION_STATUS_NEEDS_OPERATOR = "NEEDS_OPERATOR"
SUPPRESSED_NOTIFICATION_STATUSES = frozenset(
    {
        "DONE",
        "BLOCKED",
        "RETRY",
        "WAITING_DEPENDENCY",
        "PROGRESS",
        "SCHEDULED_WAKE",
        "PROVIDER_FALLBACK",
        "RECOVERY_DONE",
    }
)
ALLOWED_NOTIFICATION_STATUSES = frozenset({NOTIFICATION_STATUS_NEEDS_OPERATOR})


@dataclass(frozen=True)
class NotificationDecision:
    should_emit: bool
    status: str
    reason: str
    idempotency_key: str


def normalize_notification_status(status: object) -> str:
    if not isinstance(status, str):
        return ""
    return "_".join(status.strip().upper().replace("-", "_").split())


def notification_idempotency_key(
    *,
    source: str,
    issue_number: int | str,
    status: str,
    report: str | None = None,
) -> str:
    normalized_status = normalize_notification_status(status)
    report_head = ""
    if isinstance(report, str):
        report_head = "\n".join(report.strip().splitlines()[:8])
    digest = hashlib.sha256(
        "\n".join(
            (
                str(source),
                str(issue_number),
                normalized_status,
                report_head,
            )
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"{source}:{issue_number}:{normalized_status}:{digest}"


def evaluate_notification_policy(
    *,
    source: str,
    issue_number: int | str,
    status: object,
    report: str | None = None,
    emitted_idempotency_keys: Iterable[str] = (),
) -> NotificationDecision:
    normalized_status = normalize_notification_status(status)
    idempotency_key = notification_idempotency_key(
        source=source,
        issue_number=issue_number,
        status=normalized_status,
        report=report,
    )

    if normalized_status in SUPPRESSED_NOTIFICATION_STATUSES:
        return NotificationDecision(
            False, normalized_status, "routine_status_suppressed", idempotency_key
        )
    if normalized_status not in ALLOWED_NOTIFICATION_STATUSES:
        return NotificationDecision(
            False, normalized_status, "status_not_operator_exception", idempotency_key
        )
    if idempotency_key in set(emitted_idempotency_keys):
        return NotificationDecision(
            False, normalized_status, "duplicate_notification_suppressed", idempotency_key
        )
    return NotificationDecision(
        True, normalized_status, "operator_exception_allowed", idempotency_key
    )
