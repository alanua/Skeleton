from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence

from core.audit_ledger import validate_public_safe_payload


RUNNER_MORNING_REPORT_SCHEMA = "skeleton.runner_morning_report.v1"

_UNSAFE_TEXT_RE = re.compile(r"[\x00-\x1f\x7f<>`{}[\]|$]")
_RESULT_DONE = frozenset({"done", "completed", "success", "passed", "merged"})
_RESULT_BLOCKED = frozenset({"blocked", "failed", "failure", "error"})
_RESULT_APPROVAL = frozenset({"needs_operator_approval", "pending_operator_approval", "approval_required"})
_REVIEW_READY = frozenset({"ready_for_review", "review_ready", "open"})
_PRIVATE_EVIDENCE_KEYS = frozenset(
    {
        "artifact",
        "artifacts",
        "content",
        "evidence",
        "log",
        "logs",
        "patch",
        "private_context",
        "private_evidence",
        "raw_evidence",
        "raw_output",
        "transcript",
    }
)
_PUBLIC_SAFETY_MARKER_KEYS = frozenset({"no_private_evidence", "private_evidence_included"})


@dataclass(frozen=True)
class RunnerMorningItem:
    ref: str
    title: str
    reason: str = ""
    next_step: str = ""

    @property
    def sort_key(self) -> tuple[int, int, str, str]:
        number = _ref_number(self.ref)
        prefix_rank = 0 if self.ref.startswith("task:") else 1 if self.ref.startswith("PR #") else 2
        return (prefix_rank, number, self.title.casefold(), self.ref.casefold())

    def line(self, *, include_reason: bool = False) -> str:
        base = f"- {self.ref}: {self.title}"
        if include_reason and self.reason:
            return f"{base} - причина: {self.reason}"
        return base


@dataclass(frozen=True)
class RunnerMorningReport:
    schema: str
    completed: tuple[RunnerMorningItem, ...]
    ready_for_review: tuple[RunnerMorningItem, ...]
    blocked: tuple[RunnerMorningItem, ...]
    needs_operator_approval: tuple[RunnerMorningItem, ...]
    next_productive_work: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "completed": [_item_dict(item) for item in self.completed],
            "ready_for_review": [_item_dict(item) for item in self.ready_for_review],
            "blocked": [_item_dict(item) for item in self.blocked],
            "needs_operator_approval": [_item_dict(item) for item in self.needs_operator_approval],
            "next_productive_work": list(self.next_productive_work),
        }


def build_runner_morning_report(
    task_receipts: Sequence[Mapping[str, Any]],
    pr_receipts: Sequence[Mapping[str, Any]],
) -> RunnerMorningReport:
    """Aggregate public-safe Runner receipts without contacting GitHub or exposing evidence."""
    completed: list[RunnerMorningItem] = []
    ready_for_review: list[RunnerMorningItem] = []
    blocked: list[RunnerMorningItem] = []
    needs_approval: list[RunnerMorningItem] = []

    for receipt in task_receipts:
        _validate_receipt(receipt, receipt_type="task")
        item = _task_item(receipt)
        result = _status(receipt)
        approval_required = _bool(receipt.get("needs_operator_approval")) or result in _RESULT_APPROVAL
        reason = _safe_text(receipt.get("reason") or receipt.get("blocked_reason") or receipt.get("approval_reason"))
        next_step = _safe_text(receipt.get("next_step"))
        enriched = RunnerMorningItem(item.ref, item.title, reason=reason, next_step=next_step)
        if approval_required:
            needs_approval.append(enriched)
        elif result in _RESULT_BLOCKED or reason and result == "blocked":
            blocked.append(enriched if enriched.reason else RunnerMorningItem(item.ref, item.title, "blocked"))
        elif result in _RESULT_DONE:
            completed.append(enriched)
        elif result in _REVIEW_READY:
            ready_for_review.append(enriched)

    for receipt in pr_receipts:
        _validate_receipt(receipt, receipt_type="pr")
        item = _pr_item(receipt)
        result = _status(receipt)
        approval_required = _bool(receipt.get("needs_operator_approval")) or result in _RESULT_APPROVAL
        reason = _safe_text(receipt.get("reason") or receipt.get("blocked_reason") or receipt.get("approval_reason"))
        next_step = _safe_text(receipt.get("next_step"))
        enriched = RunnerMorningItem(item.ref, item.title, reason=reason, next_step=next_step)
        if approval_required:
            needs_approval.append(enriched)
        elif result in _RESULT_BLOCKED:
            blocked.append(enriched if enriched.reason else RunnerMorningItem(item.ref, item.title, "checks_or_review_blocked"))
        elif result in _RESULT_DONE:
            completed.append(enriched)
        elif result in _REVIEW_READY or _bool(receipt.get("ready_for_review")):
            ready_for_review.append(enriched)

    report = RunnerMorningReport(
        schema=RUNNER_MORNING_REPORT_SCHEMA,
        completed=tuple(sorted(completed, key=lambda item: item.sort_key)),
        ready_for_review=tuple(sorted(ready_for_review, key=lambda item: item.sort_key)),
        blocked=tuple(sorted(blocked, key=lambda item: item.sort_key)),
        needs_operator_approval=tuple(sorted(needs_approval, key=lambda item: item.sort_key)),
        next_productive_work=_next_productive_work(blocked, needs_approval, ready_for_review),
    )
    validate_public_safe_payload(report.as_dict())
    return report


def render_runner_morning_report(report: RunnerMorningReport) -> str:
    lines = [
        "Ранковий звіт Runner",
        "",
        "Виконано:",
        *_section_lines(report.completed),
        "",
        "Готово до review:",
        *_section_lines(report.ready_for_review),
        "",
        "Заблоковано з причиною:",
        *_section_lines(report.blocked, include_reason=True),
        "",
        "Потребує approval оператора:",
        *_section_lines(report.needs_operator_approval, include_reason=True),
        "",
        "Наступна продуктивна робота:",
        *(f"- {item}" for item in report.next_productive_work),
    ]
    return "\n".join(lines).strip() + "\n"


def render_runner_morning_report_from_receipts(
    task_receipts: Sequence[Mapping[str, Any]],
    pr_receipts: Sequence[Mapping[str, Any]],
) -> str:
    return render_runner_morning_report(build_runner_morning_report(task_receipts, pr_receipts))


def _validate_receipt(receipt: Mapping[str, Any], *, receipt_type: str) -> None:
    if not isinstance(receipt, Mapping):
        raise TypeError(f"{receipt_type} receipt must be a mapping.")
    validate_public_safe_payload(receipt)
    _reject_private_evidence_keys(receipt)
    if receipt.get("public_safe") is not True:
        raise ValueError(f"{receipt_type} receipt must declare public_safe=true.")
    if receipt.get("private_evidence_included") is True:
        raise ValueError(f"{receipt_type} receipt includes private evidence.")


def _reject_private_evidence_keys(value: Any, *, path: str = "receipt") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = key.lower()
            if lowered not in _PUBLIC_SAFETY_MARKER_KEYS and (
                lowered in _PRIVATE_EVIDENCE_KEYS or lowered.endswith("_evidence")
            ):
                raise ValueError(f"{path}.{key} is not allowed in a morning report receipt.")
            _reject_private_evidence_keys(child, path=f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_private_evidence_keys(child, path=f"{path}[{index}]")


def _task_item(receipt: Mapping[str, Any]) -> RunnerMorningItem:
    task_id = _safe_text(receipt.get("task_id") or receipt.get("issue_number") or receipt.get("id"))
    if not task_id:
        raise ValueError("task receipt requires task_id, issue_number, or id.")
    title = _safe_text(receipt.get("title") or receipt.get("summary") or "Runner task")
    return RunnerMorningItem(f"task:{task_id}", title)


def _pr_item(receipt: Mapping[str, Any]) -> RunnerMorningItem:
    number = receipt.get("pr_number") or receipt.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ValueError("PR receipt requires a positive integer pr_number.")
    title = _safe_text(receipt.get("title") or receipt.get("summary") or "Runner PR")
    return RunnerMorningItem(f"PR #{number}", title)


def _status(receipt: Mapping[str, Any]) -> str:
    raw = _safe_text(receipt.get("status") or receipt.get("result") or receipt.get("state")).lower()
    return raw.replace("-", "_").replace(" ", "_")


def _next_productive_work(
    blocked: Sequence[RunnerMorningItem],
    needs_approval: Sequence[RunnerMorningItem],
    ready_for_review: Sequence[RunnerMorningItem],
) -> tuple[str, ...]:
    candidates: list[str] = []
    for item in sorted(needs_approval, key=lambda value: value.sort_key):
        candidates.append(item.next_step or f"Оператору перевірити approval для {item.ref}.")
    for item in sorted(blocked, key=lambda value: value.sort_key):
        candidates.append(item.next_step or f"Зняти блокер для {item.ref}: {item.reason or 'причина не вказана'}.")
    for item in sorted(ready_for_review, key=lambda value: value.sort_key):
        candidates.append(item.next_step or f"Провести review для {item.ref}.")
    if not candidates:
        candidates.append("Взяти наступну public-safe задачу з runner:ready.")
    return tuple(_dedupe(candidates)[:5])


def _section_lines(items: Sequence[RunnerMorningItem], *, include_reason: bool = False) -> tuple[str, ...]:
    if not items:
        return ("- Немає.",)
    return tuple(item.line(include_reason=include_reason) for item in items)


def _item_dict(item: RunnerMorningItem) -> dict[str, str]:
    result = {"ref": item.ref, "title": item.title}
    if item.reason:
        result["reason"] = item.reason
    if item.next_step:
        result["next_step"] = item.next_step
    return result


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split()).strip()
    if len(text) > 180:
        raise ValueError("receipt text field is too long for a short public report.")
    if text and _UNSAFE_TEXT_RE.search(text):
        raise ValueError("receipt text field must be bounded public-safe text.")
    return text


def _bool(value: Any) -> bool:
    return value is True


def _ref_number(ref: str) -> int:
    match = re.search(r"\d+", ref)
    return int(match.group(0)) if match else 0


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
