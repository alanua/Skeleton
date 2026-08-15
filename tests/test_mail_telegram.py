from __future__ import annotations

from core.mail_operations import process_important_mail
from integrations.mail_telegram import build_mail_telegram_handoff, parse_mail_telegram_action


def test_mail_telegram_handoff_is_typed_and_retry_safe() -> None:
    receipt = process_important_mail(
        {
            "provider": "synthetic",
            "provider_message_ref": "msg-1",
            "thread_ref": "thread-1",
            "sender_ref": "sender-ref",
            "received_at": 1786400000,
            "subject_hint": "Important invoice",
            "body_preview": "Invoice deadline 2026-09-01",
            "deadline_hint": "2026-09-01",
        },
        now=1786400010,
    )
    handoff = build_mail_telegram_handoff(receipt["operator_packet"])
    action = {
        "schema": "skeleton.mail_telegram_action.v1",
        "action": "approve_reply",
        "correspondence_ref": handoff["correspondence_ref"],
        "approved_semantic_hash": handoff["approved_semantic_hash"],
    }

    first = parse_mail_telegram_action(handoff, action)
    second = parse_mail_telegram_action(handoff, action)

    assert first["accepted"] is True
    assert second["accepted"] is True
    assert first["idempotency_key"] == second["idempotency_key"]


def test_mail_telegram_rejects_semantic_hash_mismatch() -> None:
    receipt = process_important_mail(
        {
            "provider": "synthetic",
            "provider_message_ref": "msg-2",
            "thread_ref": "thread-2",
            "sender_ref": "sender-ref",
            "received_at": 1786400000,
            "subject_hint": "Important invoice",
            "body_preview": "Invoice deadline 2026-09-01",
            "deadline_hint": "2026-09-01",
        },
        now=1786400010,
    )
    handoff = build_mail_telegram_handoff(receipt["operator_packet"])

    parsed = parse_mail_telegram_action(
        handoff,
        {
            "schema": "skeleton.mail_telegram_action.v1",
            "action": "approve_reply",
            "correspondence_ref": handoff["correspondence_ref"],
            "approved_semantic_hash": "0" * 64,
        },
    )

    assert parsed["accepted"] is False
    assert parsed["status"] == "BLOCKED"
