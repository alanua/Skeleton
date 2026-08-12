from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
import urllib.parse
import urllib.request
from typing import Any


@dataclass(frozen=True)
class TelegramEmitReceipt:
    status: str
    reason: str
    packet_ref: str | None
    sent: bool

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "packet_ref": self.packet_ref,
            "sent": self.sent,
            "public_safe": True,
            "private_payloads_included": False,
        }


class MailTelegramEmitter:
    def emit_operator_packet(self, packet: Mapping[str, Any], *, packet_ref: str) -> TelegramEmitReceipt:
        raise NotImplementedError


class RecordingMailTelegramEmitter(MailTelegramEmitter):
    def __init__(self) -> None:
        self.packets: list[tuple[str, Mapping[str, Any]]] = []

    def emit_operator_packet(self, packet: Mapping[str, Any], *, packet_ref: str) -> TelegramEmitReceipt:
        self.packets.append((packet_ref, dict(packet)))
        return TelegramEmitReceipt("RECORDED", "PACKET_RECORDED", packet_ref, False)


class EnvTelegramEmitter(MailTelegramEmitter):
    def __init__(self, *, api_base: str = "https://api.telegram.org") -> None:
        self.api_base = api_base.rstrip("/")

    def emit_operator_packet(self, packet: Mapping[str, Any], *, packet_ref: str) -> TelegramEmitReceipt:
        token = os.environ.get("SKELETON_TG_BOT")
        chat_id = os.environ.get("SKELETON_TG_CHAT")
        if not token or not chat_id:
            return TelegramEmitReceipt("SKIPPED", "TELEGRAM_AUTH_REQUIRED", packet_ref, False)
        text = _render_packet(packet)
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
        request = urllib.request.Request(f"{self.api_base}/bot{token}/sendMessage", data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                response.read()
        except Exception:
            return TelegramEmitReceipt("FAILED", "TELEGRAM_SEND_FAILED", packet_ref, False)
        return TelegramEmitReceipt("SENT", "TELEGRAM_PACKET_SENT", packet_ref, True)


def _render_packet(packet: Mapping[str, Any]) -> str:
    summary = str(packet.get("summary_uk") or "Важлива кореспонденція потребує дії.")
    case_ref = str(packet.get("case_ref") or "case:unknown")
    corr_ref = str(packet.get("correspondence_ref") or "corr:unknown")
    return "\n".join((summary[:900], f"Case: {case_ref}", f"Correspondence: {corr_ref}"))


def public_packet_json(packet: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            "schema": packet.get("schema"),
            "case_ref": packet.get("case_ref"),
            "correspondence_ref": packet.get("correspondence_ref"),
            "actionable": packet.get("telegram_reply_contract", {}).get("actionable"),
        },
        ensure_ascii=True,
        sort_keys=True,
    )
