from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol

from core.telegram_permissions import TelegramAccessMode, TelegramGatewayError, TelegramSource


class MTProtoClient(Protocol):
    def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0) -> Iterable[Any]: ...


@dataclass(frozen=True)
class TelegramMTProtoCredentials:
    api_id_ref: str
    api_hash_ref: str
    string_session_ref: str


class TelegramMTProtoFacade:
    """Read-only MTProto boundary with lazy optional Telethon dependency."""

    _FORBIDDEN_OPERATIONS = frozenset(
        {
            "send_message",
            "edit_message",
            "delete_messages",
            "forward_messages",
            "join_channel",
            "leave_channel",
            "invite_to_channel",
            "pin_message",
            "account",
            "react",
        }
    )

    def __init__(
        self,
        source: TelegramSource,
        *,
        client_factory: Callable[[], MTProtoClient] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._source = source
        self._client_factory = client_factory
        self._secret_resolver = secret_resolver

    def read_messages(self, peer_id: str, *, limit: int | None = None, offset_id: int = 0) -> list[Mapping[str, Any]]:
        self._source.require(TelegramAccessMode.READ_ALLOWED_PRIVATE)
        self._source.require_peer(peer_id)
        client = self._client()
        bounded_limit = self._source.bounded_limit(limit)
        return [_normalize_mtproto_message(item, self._source.source_id, peer_id) for item in client.iter_messages(peer_id, limit=bounded_limit, offset_id=offset_id)]

    def unavailable_write(self, *_args: object, **_kwargs: object) -> None:
        raise TelegramGatewayError("OPERATION_NOT_AVAILABLE", "Telegram user-account writes are unavailable")

    def __getattr__(self, name: str) -> object:
        if name in self._FORBIDDEN_OPERATIONS:
            return self.unavailable_write
        raise AttributeError(name)

    def _client(self) -> MTProtoClient:
        if self._client_factory is not None:
            return self._client_factory()
        self._require_credentials()
        try:
            from telethon import TelegramClient  # type: ignore[import-not-found]
            from telethon.sessions import StringSession  # type: ignore[import-not-found]
        except Exception as exc:
            raise TelegramGatewayError("AUTH_REQUIRED", "Telethon is optional and is not installed") from exc
        api_id = self._resolve("telegram_api_id")
        api_hash = self._resolve("telegram_api_hash")
        session = self._resolve("telegram_mtproto_string_session")
        return TelegramClient(StringSession(session), int(api_id), api_hash)

    def _require_credentials(self) -> None:
        required = {"telegram_api_id", "telegram_api_hash", "telegram_mtproto_string_session"}
        if not required <= set(self._source.secret_refs):
            raise TelegramGatewayError("AUTH_REQUIRED", "MTProto secret refs are incomplete")

    def _resolve(self, ref: str) -> str:
        if self._secret_resolver is None:
            raise TelegramGatewayError("AUTH_REQUIRED", "MTProto credentials require Home Edge to Bitwarden resolution")
        value = self._secret_resolver(ref)
        if not value:
            raise TelegramGatewayError("AUTH_REQUIRED", "MTProto credential is unavailable")
        return value


def _normalize_mtproto_message(item: Any, source_id: str, peer_id: str) -> dict[str, Any]:
    message_id = getattr(item, "id", None)
    text = getattr(item, "message", None) or getattr(item, "text", "")
    date = getattr(item, "date", None)
    edited_at = getattr(item, "edit_date", None)
    media = getattr(item, "media", None)
    return {
        "source_id": source_id,
        "peer_id": str(peer_id),
        "message_id": int(message_id),
        "text": str(text or ""),
        "sent_at": date.isoformat() if hasattr(date, "isoformat") else None,
        "edited_at": edited_at.isoformat() if hasattr(edited_at, "isoformat") else None,
        "deleted_at": None,
        "media": _media_metadata(media),
    }


def _media_metadata(media: object) -> dict[str, object] | None:
    if media is None:
        return None
    return {
        "kind": type(media).__name__,
        "downloaded": False,
    }
