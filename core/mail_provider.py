from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any, Final, Protocol

from core.mail_operations import MailEnvelope, MailOperationError


MAIL_PROVIDER_ACCOUNT_SCHEMA: Final = "skeleton.mail_provider_account.v1"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+,-]{0,255}$")


@dataclass(frozen=True)
class MailProviderAccount:
    account_ref: str
    provider: str
    poll_interval_seconds: int
    max_messages_per_poll: int
    query: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MailProviderAccount":
        if not isinstance(value, Mapping):
            raise MailOperationError("INVALID_PROVIDER_ACCOUNT", "account must be an object")
        if value.get("schema") != MAIL_PROVIDER_ACCOUNT_SCHEMA:
            raise MailOperationError("INVALID_PROVIDER_ACCOUNT_SCHEMA", "invalid account schema")
        return cls(
            account_ref=_safe_ref(value.get("account_ref"), "account_ref"),
            provider=_safe_token(value.get("provider"), "provider"),
            poll_interval_seconds=_positive_int(
                value.get("poll_interval_seconds"), "poll_interval_seconds"
            ),
            max_messages_per_poll=_bounded_positive_int(
                value.get("max_messages_per_poll"), "max_messages_per_poll", limit=100
            ),
            query=_bounded_text(value.get("query", "newer_than:7d"), "query", 512),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": MAIL_PROVIDER_ACCOUNT_SCHEMA,
            "account_ref": self.account_ref,
            "provider": self.provider,
            "poll_interval_seconds": self.poll_interval_seconds,
            "max_messages_per_poll": self.max_messages_per_poll,
            "query": self.query,
        }


@dataclass(frozen=True)
class MailProviderCursor:
    account_ref: str
    cursor_ref: str | None = None


@dataclass(frozen=True)
class MailProviderBatch:
    account_ref: str
    provider: str
    messages: tuple[MailEnvelope, ...]
    next_cursor_ref: str | None


class MailProvider(Protocol):
    provider: str

    def poll(
        self,
        account: MailProviderAccount,
        cursor: MailProviderCursor,
        *,
        max_messages: int,
    ) -> MailProviderBatch:
        """Return normalized provider messages without sending or mutating live mail."""


class StaticMailProvider:
    """Deterministic provider used by tests and dry-run fixtures."""

    provider = "static"

    def __init__(self, messages: Sequence[Mapping[str, Any]]) -> None:
        self._messages = tuple(MailEnvelope.from_mapping(item) for item in messages)

    def poll(
        self,
        account: MailProviderAccount,
        cursor: MailProviderCursor,
        *,
        max_messages: int,
    ) -> MailProviderBatch:
        if cursor.account_ref != account.account_ref:
            raise MailOperationError("MAIL_CURSOR_ACCOUNT_MISMATCH", "cursor account mismatch")
        limited = self._messages[:max_messages]
        next_cursor = limited[-1].provider_message_ref if limited else cursor.cursor_ref
        return MailProviderBatch(
            account_ref=account.account_ref,
            provider=self.provider,
            messages=tuple(limited),
            next_cursor_ref=next_cursor,
        )


def _safe_token(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise MailOperationError("INVALID_TOKEN", f"{field} must be a safe token")
    return value


def _safe_ref(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SAFE_REF_RE.fullmatch(value) is None:
        raise MailOperationError("INVALID_REF", f"{field} must be a safe ref")
    return value


def _bounded_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise MailOperationError("INVALID_TEXT", f"{field} must be text")
    normalized = " ".join(value.split())
    if len(normalized) > limit:
        raise MailOperationError("INVALID_TEXT", f"{field} must be bounded")
    return normalized


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MailOperationError("INVALID_INTEGER", f"{field} must be positive")
    return value


def _bounded_positive_int(value: Any, field: str, *, limit: int) -> int:
    number = _positive_int(value, field)
    if number > limit:
        raise MailOperationError("INVALID_INTEGER", f"{field} must be <= {limit}")
    return number
