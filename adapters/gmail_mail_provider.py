from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from core.mail_provider import (
    MailProviderAccount,
    MailProviderError,
    MailProviderMessage,
    MailProviderPollResult,
    auth_required_result,
)


class GmailMailProvider:
    """Gmail provider boundary.

    The production transport is injected by the private runtime. This adapter
    never accepts raw credential values in public config and never performs
    mailbox mutations; cleanup remains a separate future authority handoff.
    """

    provider_name = "gmail"

    def __init__(
        self,
        *,
        transport: Callable[[MailProviderAccount, str | None, int], Mapping[str, Any]] | None = None,
    ) -> None:
        self._transport = transport

    def poll(
        self, account: MailProviderAccount, *, cursor: str | None, now: int
    ) -> MailProviderPollResult:
        if account.provider != self.provider_name:
            raise MailProviderError("PROVIDER_MISMATCH", "account provider is not gmail")
        if account.secret_reference is None:
            return auth_required_result()
        if self._transport is None:
            return auth_required_result("AUTH_REQUIRED")
        raw = self._transport(account, cursor, now)
        return _poll_result_from_mapping(raw)


def _poll_result_from_mapping(value: Mapping[str, Any]) -> MailProviderPollResult:
    if not isinstance(value, Mapping):
        raise MailProviderError("INVALID_PROVIDER_RESPONSE", "provider response must be an object")
    status = value.get("status", "OK")
    if status == "AUTH_REQUIRED":
        return auth_required_result()
    if status != "OK":
        raise MailProviderError("PROVIDER_POLL_FAILED", "provider poll failed")
    messages = value.get("messages", ())
    if not isinstance(messages, list | tuple):
        raise MailProviderError("INVALID_PROVIDER_RESPONSE", "messages must be a list")
    next_cursor = value.get("next_cursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise MailProviderError("INVALID_PROVIDER_RESPONSE", "next_cursor must be text")
    return MailProviderPollResult(
        status="OK",
        reason=str(value.get("reason") or "OK"),
        next_cursor=next_cursor,
        messages=tuple(_message_from_mapping(item) for item in messages),
    )


def _message_from_mapping(value: Mapping[str, Any]) -> MailProviderMessage:
    if not isinstance(value, Mapping):
        raise MailProviderError("INVALID_MESSAGE", "message must be an object")
    return MailProviderMessage(
        provider="gmail",
        provider_message_id=_text(value.get("id"), "id"),
        provider_thread_id=_optional_text(value.get("thread_id"), "thread_id"),
        history_id=_optional_text(value.get("history_id"), "history_id"),
        received_at=_timestamp(value.get("received_at"), "received_at"),
        subject=_text(value.get("subject", "No subject"), "subject"),
        body_preview=_text(value.get("body_preview", "No preview"), "body_preview"),
        sender=_optional_text(value.get("sender"), "sender"),
        labels=tuple(_text(item, "label") for item in value.get("labels", ())),
        attachment_refs=tuple(_text(item, "attachment_ref") for item in value.get("attachment_refs", ())),
        headers={
            _text(key, "header_name").lower(): _text(item, "header_value")
            for key, item in dict(value.get("headers", {})).items()
        },
    )


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise MailProviderError("INVALID_TEXT", f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 4096:
        raise MailProviderError("INVALID_TEXT", f"{field} must be non-empty and bounded")
    return normalized


def _optional_text(value: Any, field: str) -> str | None:
    return None if value is None else _text(value, field)


def _timestamp(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MailProviderError("INVALID_TIMESTAMP", f"{field} must be a non-negative integer")
    return value
