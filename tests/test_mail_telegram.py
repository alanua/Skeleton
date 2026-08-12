import json
from unittest import mock

from integrations.mail_telegram import EnvTelegramEmitter, RecordingMailTelegramEmitter, public_packet_json


def _packet():
    return {
        "schema": "skeleton.mail_operations.operator_packet.v1",
        "case_ref": "case:abc",
        "correspondence_ref": "corr:def",
        "summary_uk": "Важлива кореспонденція потребує дії.",
        "telegram_reply_contract": {"actionable": True},
    }


def test_recording_telegram_emitter_records_packet_without_network():
    emitter = RecordingMailTelegramEmitter()

    receipt = emitter.emit_operator_packet(_packet(), packet_ref="mail_packet:abc")

    assert receipt.status == "RECORDED"
    assert receipt.sent is False
    assert len(emitter.packets) == 1


def test_env_telegram_emitter_skips_without_auth(monkeypatch):
    monkeypatch.delenv("SKELETON_TG_BOT", raising=False)
    monkeypatch.delenv("SKELETON_TG_CHAT", raising=False)

    receipt = EnvTelegramEmitter().emit_operator_packet(_packet(), packet_ref="mail_packet:abc")

    assert receipt.status == "SKIPPED"
    assert receipt.reason == "TELEGRAM_AUTH_REQUIRED"


def test_env_telegram_emitter_does_not_leak_token_in_receipt(monkeypatch):
    monkeypatch.setenv("SKELETON_TG_BOT", "telegram-secret-token")
    monkeypatch.setenv("SKELETON_TG_CHAT", "chat-id")

    with mock.patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b"{}"
        receipt = EnvTelegramEmitter().emit_operator_packet(_packet(), packet_ref="mail_packet:abc")

    rendered = json.dumps(receipt.to_public_mapping(), sort_keys=True)
    assert receipt.status == "SENT"
    assert "telegram-secret-token" not in rendered


def test_public_packet_json_is_bounded():
    rendered = public_packet_json(_packet())

    assert "summary_uk" not in rendered
    assert "case:abc" in rendered
