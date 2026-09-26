from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Iterable, Mapping, Protocol

from core.telegram_permissions import TelegramAccessMode, TelegramGatewayError, TelegramSource


class MTProtoClient(Protocol):
    def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0, min_id: int = 0) -> Iterable[Any]: ...


@dataclass(frozen=True)
class TelegramMTProtoCredentials:
    api_id_ref: str
    api_hash_ref: str
    string_session_ref: str


class TelegramFloodWaitError(TelegramGatewayError):
    def __init__(self, seconds: int) -> None:
        super().__init__("FLOOD_WAIT", "Telegram MTProto flood wait")
        self.seconds = max(0, int(seconds))


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

    def read_messages(self, peer_id: str, *, limit: int | None = None, offset_id: int = 0, min_id: int = 0, public: bool = False) -> list[Mapping[str, Any]]:
        if public:
            self._source.require(TelegramAccessMode.READ_PUBLIC)
            peer_id = self._source.require_public_ref(peer_id)
        else:
            self._source.require(TelegramAccessMode.READ_ALLOWED_PRIVATE)
            self._source.require_peer(peer_id)
        client = self._client()
        bounded_limit = self._source.bounded_limit(limit)
        try:
            try:
                items = client.iter_messages(peer_id, limit=bounded_limit, offset_id=offset_id, min_id=min_id)
            except TypeError:
                items = client.iter_messages(peer_id, limit=bounded_limit, offset_id=offset_id)
            return [_normalize_mtproto_message(item, self._source, peer_id) for item in items]
        except Exception as exc:
            seconds = getattr(exc, "seconds", None) or getattr(exc, "value", None)
            if seconds is not None or "FloodWait" in type(exc).__name__:
                raise TelegramFloodWaitError(int(seconds or 0)) from exc
            raise

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


def _normalize_mtproto_message(item: Any, source: TelegramSource, peer_id: str) -> dict[str, Any]:
    message_id = getattr(item, "id", None)
    text = getattr(item, "message", None) or getattr(item, "text", "")
    date = getattr(item, "date", None)
    edited_at = getattr(item, "edit_date", None)
    media = getattr(item, "media", None)
    sender = getattr(item, "sender", None) or getattr(item, "from_user", None)
    reply_to = getattr(item, "reply_to", None)
    fwd_from = getattr(item, "fwd_from", None) or getattr(item, "forward", None)
    entities = [_entity(entity) for entity in list(getattr(item, "entities", None) or [])]
    urls = _urls(str(text or ""), entities)
    thread_id = getattr(reply_to, "forum_topic_id", None) or getattr(reply_to, "reply_to_top_id", None) or getattr(item, "thread_id", None)
    caption = getattr(item, "caption", None)
    return {
        "source_id": source.source_id,
        "peer_id": str(peer_id),
        "peer_channel_id": source.peer_id,
        "username": _username(source.handle),
        "title": source.title,
        "message_id": int(message_id),
        "text": str(text or ""),
        "sent_at": date.isoformat() if hasattr(date, "isoformat") else None,
        "edited_at": edited_at.isoformat() if hasattr(edited_at, "isoformat") else None,
        "deleted_at": None,
        "sender_id": _text(getattr(sender, "id", None) or getattr(item, "sender_id", None)),
        "sender_username": _text(getattr(sender, "username", None)),
        "sender_name": _sender_name(sender),
        "author": _text(getattr(item, "post_author", None) or getattr(item, "author", None)),
        "entities": entities,
        "urls": urls,
        "reply_to_message_id": getattr(reply_to, "reply_to_msg_id", None) or getattr(item, "reply_to_msg_id", None),
        "thread_id": thread_id,
        "forwarded_from": _forward(fwd_from),
        "media": _media_metadata(media, caption=caption),
        "permalink": _permalink(source.handle, message_id),
        "raw": {
            "message_class": type(item).__name__,
            "media_class": type(media).__name__ if media is not None else None,
        },
    }


def _media_metadata(media: object, *, caption: object = None) -> dict[str, object] | None:
    if media is None and not caption:
        return None
    metadata: dict[str, object] = {
        "kind": type(media).__name__ if media is not None else "caption",
        "downloaded": False,
    }
    size = getattr(media, "size", None)
    if size is not None:
        metadata["size_bytes"] = size
    if caption:
        metadata["caption"] = str(caption)
    return metadata


def _entity(entity: object) -> dict[str, object]:
    return {
        "type": type(entity).__name__,
        "offset": getattr(entity, "offset", None),
        "length": getattr(entity, "length", None),
        "url": getattr(entity, "url", None),
    }


def _urls(text: str, entities: list[Mapping[str, object]]) -> list[str]:
    found = [str(entity["url"]) for entity in entities if entity.get("url")]
    found.extend(match.group(0) for match in re.finditer(r"https?://[^\s)>\]]+", text))
    deduped: list[str] = []
    for url in found:
        if url not in deduped:
            deduped.append(url)
    return deduped


def _forward(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "from_id": _text(getattr(value, "from_id", None)),
        "from_name": _text(getattr(value, "from_name", None)),
        "channel_id": _text(getattr(value, "channel_id", None)),
        "date": getattr(getattr(value, "date", None), "isoformat", lambda: None)(),
    }


def _permalink(handle: str, message_id: object) -> str | None:
    if not handle.startswith("@") or message_id is None:
        return None
    return f"https://t.me/{handle[1:]}/{int(message_id)}"


def _username(handle: str) -> str | None:
    return handle[1:] if handle.startswith("@") else None


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _sender_name(sender: object) -> str | None:
    if sender is None:
        return None
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join(str(part) for part in parts if part)
    return name or _text(getattr(sender, "title", None))
