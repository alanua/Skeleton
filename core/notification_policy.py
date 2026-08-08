from __future__ import annotations

import re


ROUTINE_REVIEW_VERDICTS = frozenset({"APPROVE", "REQUEST_CHANGES", "DO_NOT_MERGE"})
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
    normalized = (status or "").strip().upper()
    if normalized == "NEEDS_OPERATOR":
        return True
    if normalized in {"DONE", "BLOCKED"} and routine_review_outcome(report):
        return False
    return normalized in {"DONE", "BLOCKED"}
