from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import threading
from typing import Any, Callable, Iterable, Mapping, Protocol

from core.telegram_permissions import TelegramAccessMode, TelegramGatewayError, TelegramSource


class MTProtoClient(Protocol):
    def iter_messages(self, peer_id: str, *, limit: int, offset_id: int = 0, min_id: int = 0) -> Iterable[Any]: ...


SecretResolver = Callable[[str], str | None]
AuthorizationProvider = Callable[[TelegramSource, tuple[str, ...]], Mapping[str, str] | None]


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
        authorization_provider: AuthorizationProvider | None = None,
    ) -> None:
        self._source = source
        self._client_factory = client_factory
        self._secret_resolver = secret_resolver
        self._authorization_provider = authorization_provider
        self._client_instance: MTProtoClient | None = None

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
                items = self._call(client.iter_messages, peer_id, limit=bounded_limit, offset_id=offset_id, min_id=min_id)
            except TypeError:
                items = self._call(client.iter_messages, peer_id, limit=bounded_limit, offset_id=offset_id)
            return [_normalize_mtproto_message(item, self._source, peer_id) for item in _drain_iterable(items)]
        except Exception as exc:
            seconds = getattr(exc, "seconds", None) or getattr(exc, "value", None)
            if seconds is not None or "FloodWait" in type(exc).__name__:
                raise TelegramFloodWaitError(int(seconds or 0)) from exc
            if isinstance(exc, TelegramGatewayError):
                raise
            raise TelegramGatewayError("BLOCKED", "Telegram MTProto read failed") from exc

    def read_updates(self, peer_id: str, *, limit: int | None = None, marker: object = None, public: bool = False) -> list[Mapping[str, Any]]:
        if public:
            self._source.require(TelegramAccessMode.READ_PUBLIC)
            peer_id = self._source.require_public_ref(peer_id)
        else:
            self._source.require(TelegramAccessMode.READ_ALLOWED_PRIVATE)
            self._source.require_peer(peer_id)
        client = self._client()
        update_reader = getattr(client, "iter_updates", None) or getattr(client, "get_updates", None)
        if update_reader is None:
            return []
        bounded_limit = self._source.bounded_limit(limit)
        try:
            updates = self._call(update_reader, peer_id, limit=bounded_limit, marker=marker)
        except TypeError:
            updates = self._call(update_reader, peer_id, limit=bounded_limit)
        try:
            return [_normalize_update(update, self._source, peer_id) for update in _drain_iterable(updates)]
        except Exception as exc:
            seconds = getattr(exc, "seconds", None) or getattr(exc, "value", None)
            if seconds is not None or "FloodWait" in type(exc).__name__:
                raise TelegramFloodWaitError(int(seconds or 0)) from exc
            if isinstance(exc, TelegramGatewayError):
                raise
            raise TelegramGatewayError("BLOCKED", "Telegram MTProto update read failed") from exc

    def unavailable_write(self, *_args: object, **_kwargs: object) -> None:
        raise TelegramGatewayError("OPERATION_NOT_AVAILABLE", "Telegram user-account writes are unavailable")

    def __getattr__(self, name: str) -> object:
        if name in self._FORBIDDEN_OPERATIONS:
            return self.unavailable_write
        raise AttributeError(name)

    def _client(self) -> MTProtoClient:
        if self._client_instance is not None:
            return self._client_instance
        if self._client_factory is not None:
            client = self._client_factory()
            self._ensure_connected(client)
            self._client_instance = client
            return client
        self._require_credentials()
        try:
            from telethon import TelegramClient  # type: ignore[import-not-found]
            from telethon.sessions import StringSession  # type: ignore[import-not-found]
        except Exception as exc:
            raise TelegramGatewayError("AUTH_REQUIRED", "Telethon is optional and is not installed") from exc
        api_id = self._resolve("telegram_api_id")
        api_hash = self._resolve("telegram_api_hash")
        session = self._resolve("telegram_mtproto_string_session")
        client = TelegramClient(StringSession(session), int(api_id), api_hash)
        self._ensure_connected(client)
        self._client_instance = client
        return client

    def _ensure_connected(self, client: object) -> None:
        connected = getattr(client, "is_connected", None)
        if callable(connected) and connected() is False:
            connect = getattr(client, "connect", None)
            if callable(connect):
                self._call(connect)
        elif not callable(connected):
            connect = getattr(client, "connect", None)
            if callable(connect):
                self._call(connect)
        authorized = getattr(client, "is_user_authorized", None)
        if callable(authorized) and self._call(authorized) is False:
            raise TelegramGatewayError("AUTH_REQUIRED", "Telegram MTProto session is not authorized")

    def _call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return _await_sync(func(*args, **kwargs))

    def _require_credentials(self) -> None:
        required = {"telegram_api_id", "telegram_api_hash", "telegram_mtproto_string_session"}
        if not required <= set(self._source.secret_refs):
            raise TelegramGatewayError("AUTH_REQUIRED", "MTProto secret refs are incomplete")

    def _resolve(self, ref: str) -> str:
        if self._secret_resolver is None:
            raise TelegramGatewayError("AUTH_REQUIRED", "MTProto credentials require Home Edge to Bitwarden resolution")
        value = self._secret_resolver(ref)
        if not value and self._authorization_provider is not None and ref == "telegram_mtproto_string_session":
            material = self._authorization_provider(self._source, tuple(self._source.secret_refs)) or {}
            value = material.get(ref)
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


def _normalize_update(update: Any, source: TelegramSource, peer_id: str) -> dict[str, Any]:
    marker = getattr(update, "pts", None) or getattr(update, "marker", None)
    deleted_ids = getattr(update, "deleted_ids", None) or getattr(update, "messages", None)
    class_name = type(update).__name__.lower()
    if deleted_ids is not None and "delete" in class_name:
        ids = [int(value) for value in list(deleted_ids)]
        return {"kind": "delete", "message_ids": ids, "marker": marker}
    message = getattr(update, "message", None)
    if message is None and hasattr(update, "id"):
        message = update
    if message is None:
        return {"kind": "noop", "marker": marker}
    kind = "edit" if "edit" in class_name else "new"
    return {"kind": kind, "message": _normalize_mtproto_message(message, source, peer_id), "marker": marker}


def _await_sync(value: Any) -> Any:
    if not hasattr(value, "__await__"):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(value)
        except BaseException as exc:  # pragma: no cover - exercised only inside active event loops
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _drain_iterable(items: Any) -> list[Any]:
    items = _await_sync(items)
    if hasattr(items, "__aiter__"):
        async def collect() -> list[Any]:
            return [item async for item in items]

        return _await_sync(collect())
    return list(items)


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
