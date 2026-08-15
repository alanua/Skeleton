from __future__ import annotations

import urllib.error

import pytest

from core.telegram_notifications import TelegramNotificationError, send_telegram_notification


def test_missing_credentials_fail_closed() -> None:
    with pytest.raises(TelegramNotificationError):
        send_telegram_notification("hello", env={})


def test_sender_uses_canonical_telegram_env() -> None:
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def opener(request, *, timeout):
        calls.append((request, timeout))
        return Response()

    send_telegram_notification(
        "hello",
        env={"SKELETON_TG_BOT": "token", "SKELETON_TG_CHAT": "chat"},
        opener=opener,
    )

    request, timeout = calls[0]
    assert request.full_url == "https://api.telegram.org/bottoken/sendMessage"
    assert request.get_method() == "POST"
    assert timeout == 10


def test_transport_failure_raises_notification_error() -> None:
    def opener(_request, *, timeout):
        raise urllib.error.URLError("offline")

    with pytest.raises(TelegramNotificationError):
        send_telegram_notification(
            "hello",
            env={"SKELETON_TG_BOT": "token", "SKELETON_TG_CHAT": "chat"},
            opener=opener,
        )
