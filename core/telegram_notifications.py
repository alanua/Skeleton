from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping


TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT_SECONDS = 10
TELEGRAM_BOT_ENV = "SKELETON_TG_BOT"
TELEGRAM_CHAT_ENV = "SKELETON_TG_CHAT"


class TelegramNotificationError(RuntimeError):
    """Raised when the canonical Skeleton Telegram sender cannot deliver."""


def send_telegram_notification(
    message: str,
    reply_markup: Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    opener: Callable[..., Any] | None = None,
    timeout_seconds: int = TELEGRAM_TIMEOUT_SECONDS,
) -> None:
    """Send one Skeleton Telegram notification through the canonical bot env."""
    source_env = os.environ if env is None else env
    bot_token = source_env.get(TELEGRAM_BOT_ENV)
    chat_id = source_env.get(TELEGRAM_CHAT_ENV)
    if not bot_token or not chat_id:
        raise TelegramNotificationError("telegram credentials are missing")
    if not message or len(message) > 4096:
        raise TelegramNotificationError("telegram message is empty or too large")

    fields: dict[str, str] = {
        "chat_id": str(chat_id),
        "text": message,
        "disable_web_page_preview": "true",
    }
    if reply_markup is not None:
        fields["reply_markup"] = json.dumps(
            dict(reply_markup),
            sort_keys=True,
            separators=(",", ":"),
        )
    request = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout_seconds):
            return
    except Exception as exc:
        raise TelegramNotificationError(type(exc).__name__) from exc
