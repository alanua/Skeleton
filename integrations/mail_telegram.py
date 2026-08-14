from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_telegram_operator_packet(operator_packet: Mapping[str, Any]) -> dict[str, Any]:
    """Render a Telegram-ready packet without sending it."""

    return {
        "schema": "skeleton.mail_telegram.operator_packet.v1",
        "status": "READY",
        "case_ref": str(operator_packet["case_ref"]),
        "correspondence_ref": str(operator_packet["correspondence_ref"]),
        "language": "uk",
        "summary_uk": str(operator_packet["summary_uk"]),
        "reply_contract": dict(operator_packet["telegram_reply_contract"]),
        "public_safe": True,
        "private_payloads_included": False,
        "telegram_send_executed": False,
    }
