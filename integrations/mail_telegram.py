from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any, Final


MAIL_TELEGRAM_HANDOFF_SCHEMA: Final = "skeleton.mail_telegram_handoff.v1"
MAIL_TELEGRAM_ACTION_SCHEMA: Final = "skeleton.mail_telegram_action.v1"

_ACTIONS = frozenset({"approve_reply", "revise_reply", "defer", "confirm_deadline"})


def build_mail_telegram_handoff(operator_packet: Mapping[str, Any]) -> dict[str, Any]:
    case_ref = str(operator_packet.get("case_ref") or "")
    correspondence_ref = str(operator_packet.get("correspondence_ref") or "")
    semantic_hash = str(operator_packet.get("approved_semantic_hash") or "")
    return {
        "schema": MAIL_TELEGRAM_HANDOFF_SCHEMA,
        "case_ref": case_ref,
        "correspondence_ref": correspondence_ref,
        "summary_uk": str(operator_packet.get("summary_uk") or ""),
        "draft_ref": operator_packet.get("draft_ref"),
        "approved_semantic_hash": semantic_hash,
        "allowed_actions": tuple(
            action["id"]
            for action in operator_packet.get("telegram_reply_contract", {}).get(
                "allowed_actions", []
            )
            if isinstance(action, Mapping) and action.get("id") in _ACTIONS
        ),
        "idempotency_key": _hash(
            {
                "case_ref": case_ref,
                "correspondence_ref": correspondence_ref,
                "semantic_hash": semantic_hash,
            }
        )[:32],
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def parse_mail_telegram_action(
    handoff: Mapping[str, Any],
    action_packet: Mapping[str, Any],
) -> dict[str, Any]:
    action = str(action_packet.get("action") or "")
    allowed = frozenset(str(item) for item in handoff.get("allowed_actions", ()))
    accepted = (
        handoff.get("schema") == MAIL_TELEGRAM_HANDOFF_SCHEMA
        and action_packet.get("schema") == MAIL_TELEGRAM_ACTION_SCHEMA
        and action in allowed
        and action_packet.get("correspondence_ref") == handoff.get("correspondence_ref")
    )
    semantic_hash = action_packet.get("approved_semantic_hash")
    if action == "approve_reply":
        accepted = accepted and semantic_hash == handoff.get("approved_semantic_hash")
    return {
        "schema": "skeleton.mail_telegram_action_receipt.v1",
        "accepted": accepted,
        "status": "DONE" if accepted else "BLOCKED",
        "reason": "MAIL_TELEGRAM_ACTION_ACCEPTED" if accepted else "MAIL_TELEGRAM_ACTION_REJECTED",
        "action": action,
        "correspondence_ref": action_packet.get("correspondence_ref"),
        "idempotency_key": _hash(
            {
                "handoff": handoff.get("idempotency_key"),
                "action": action,
                "correspondence_ref": action_packet.get("correspondence_ref"),
                "semantic_hash": semantic_hash,
            }
        )[:32],
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
