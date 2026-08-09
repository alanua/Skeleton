from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any, Final

from core.scheduler_store import SchedulerStore


ROUTINE_REVIEW_VERDICTS = frozenset({"APPROVE", "REQUEST_CHANGES", "DO_NOT_MERGE"})
ROUTINE_STATUSES: Final = frozenset({"DONE", "BLOCKED", "RETRY", "WAITING", "REVIEW"})
NEEDS_OPERATOR: Final = "NEEDS_OPERATOR"
NOTIFICATION_EVENT_KIND: Final = "operator_notification.v1"
_PR_READY_RE = re.compile(
    r"(Draft PR:|PR ready for operator review|очікує схвалення|Перевір у ChatGPT)",
    re.IGNORECASE,
)
_INTERNAL_REVIEW_RE = re.compile(r"internal_review_verdict=(APPROVE|REQUEST_CHANGES|DO_NOT_MERGE)")


def routine_review_outcome(report: str | None) -> bool:
    text = report or ""
    if _INTERNAL_REVIEW_RE.search(text):
        return True
    return bool(_PR_READY_RE.search(text))


def should_notify_operator_for_runner_result(status: str, report: str | None) -> bool:
    normalized = normalized_status(status)
    if normalized == NEEDS_OPERATOR:
        return True
    if normalized in ROUTINE_STATUSES:
        return False
    return False


def normalized_status(status: str) -> str:
    return str(status or "").strip().upper()


def operator_notification_ledger_key(
    *,
    issue_number: int | None = None,
    repository: str | None = None,
    pr_number: int | None = None,
    head_sha: str | None = None,
    reason: str | None = None,
) -> str:
    payload = {
        "issue_number": issue_number,
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": None if head_sha is None else head_sha.lower(),
        "reason": reason,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "notify:" + hashlib.sha256(encoded).hexdigest()[:40]


def should_notify_operator_status(status: str) -> bool:
    return normalized_status(status) == NEEDS_OPERATOR


def claim_operator_notification(
    store: SchedulerStore,
    *,
    status: str,
    now: int,
    payload: Mapping[str, Any],
    issue_number: int | None = None,
    repository: str | None = None,
    pr_number: int | None = None,
    head_sha: str | None = None,
    reason: str | None = None,
) -> bool:
    if not should_notify_operator_status(status):
        return False
    store.initialize()
    ledger_key = operator_notification_ledger_key(
        issue_number=issue_number,
        repository=repository,
        pr_number=pr_number,
        head_sha=head_sha,
        reason=reason,
    )
    _, created = store.record_operational_event_once(
        ledger_key=ledger_key,
        event_kind=NOTIFICATION_EVENT_KIND,
        payload={
            "schema": "skeleton.operator_notification_claim.v1",
            "status": NEEDS_OPERATOR,
            "issue_number": issue_number,
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": None if head_sha is None else head_sha.lower(),
            "reason": reason,
            "notification_payload": dict(payload),
            "public_safe": True,
            "external_side_effects_executed": False,
        },
        now=now,
    )
    return created
