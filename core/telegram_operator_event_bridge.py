from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.operator_event import OperatorEvent
from core.telegram_approval_buttons import BUTTON_ACTIONS, validate_callback_payload


BRIDGE_SCHEMA = "skeleton.telegram_operator_event_bridge.result.v1"


@dataclass(frozen=True)
class TelegramOperatorEventBridgeResult:
    """Deterministic dry-run Telegram callback audit-trail rendering result."""

    status: str
    event: dict[str, object]
    issue_comment_text: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": BRIDGE_SCHEMA,
            "status": self.status,
            "event": self.event,
            "issue_comment_text": self.issue_comment_text,
            "reasons": list(self.reasons),
        }


def bridge_callback_to_operator_event(
    callback_payload: Mapping[str, object],
    *,
    repo: str,
    issue_number: int,
    pr_number: int,
    current_head_sha: str,
    expected_files: tuple[str, ...],
    actor_reference: str,
    timestamp: str,
) -> TelegramOperatorEventBridgeResult:
    """Render one validated Telegram callback as a dry-run operator event."""
    decision = validate_callback_payload(
        callback_payload,
        current_head_sha=current_head_sha,
        expected_files=expected_files,
    )
    reasons = list(decision.reasons)

    if decision.repo is not None and decision.repo != repo:
        reasons.append("callback repo does not match current PR state.")
    if decision.pr_number is not None and decision.pr_number != pr_number:
        reasons.append("callback pr_number does not match current PR state.")

    action = decision.action if decision.action in BUTTON_ACTIONS else "invalid_callback"
    status = _bridge_status(action, decision.status, tuple(reasons))
    event = OperatorEvent(
        repo=repo,
        issue_number=issue_number,
        pr_number=pr_number,
        head_sha=current_head_sha,
        event_type="operator_console_interaction",
        action_name=f"telegram_{action}",
        result=status,
        source="telegram_callback",
        actor_reference=actor_reference,
        timestamp=timestamp,
        summary=_summary_for(status, action),
    )
    return TelegramOperatorEventBridgeResult(
        status=status,
        event=event.to_dict(),
        issue_comment_text=event.render_public_issue_comment(),
        reasons=tuple(reasons),
    )


def _bridge_status(action: str, callback_status: str, reasons: tuple[str, ...]) -> str:
    if callback_status != "validated" or reasons:
        return "blocked"
    if action == "approve":
        return "validated"
    return "dry_run"


def _summary_for(status: str, action: str) -> str:
    if status == "blocked":
        return "Telegram callback was blocked during stage 1 dry-run validation; no live action was executed."
    if action == "approve":
        return "Telegram approve callback was validated for a stage 1 dry-run operator event; no merge was executed."
    return f"Telegram {action} callback produced a stage 1 dry-run operator event; no live action was executed."
