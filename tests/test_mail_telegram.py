import json

from core.mail_operations import process_important_mail
from integrations.mail_telegram import build_telegram_operator_packet


def test_mail_telegram_packet_is_actionable_and_public_safe() -> None:
    receipt = process_important_mail(
        {
            "provider": "gmail",
            "provider_message_ref": "mailmsg:abc",
            "received_at": 1786400000,
            "subject_hint": "Important private subject",
            "body_preview": "Deadline 2026-09-01 private body",
            "importance_hint": "important",
        },
        now=1786400010,
    )

    packet = build_telegram_operator_packet(receipt["operator_packet"])
    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True)

    assert packet["status"] == "READY"
    assert packet["reply_contract"]["actionable"] is True
    assert packet["telegram_send_executed"] is False
    assert "private subject" not in encoded
    assert "private body" not in encoded
