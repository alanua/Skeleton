from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.telegram_audit import TelegramAuditLog, provenance_hash
from core.telegram_memory_bridge import TelegramMemoryProposalBridge
from core.telegram_mtproto import TelegramFloodWaitError, TelegramMTProtoFacade
from core.telegram_notifications import TelegramNotificationError, send_telegram_notification
from core.telegram_permissions import (
    TelegramAccessMode,
    TelegramAllowlist,
    TelegramGatewayError,
    TelegramSource,
)
from core.telegram_retrieval import TelegramRetrieval
from core.telegram_store import TelegramStore


TELEGRAM_GATEWAY_SCHEMA = "skeleton.telegram_gateway.v1"
PUBLIC_INTEGRATION_SOURCE = "@midnightquantum"


@dataclass(frozen=True)
class TelegramMedia:
    kind: str
    downloaded: bool = False
    size_bytes: int | None = None
    caption: str | None = None


@dataclass(frozen=True)
class TelegramMessage:
    source_id: str
    peer_id: str
    message_id: int
    text: str
    peer_channel_id: str | None = None
    username: str | None = None
    title: str | None = None
    sent_at: str | None = None
    edited_at: str | None = None
    deleted_at: str | None = None
    sender_id: str | None = None
    sender_username: str | None = None
    sender_name: str | None = None
    author: str | None = None
    entities: tuple[Mapping[str, object], ...] = ()
    urls: tuple[str, ...] = ()
    reply_to_message_id: int | None = None
    thread_id: int | None = None
    forwarded_from: Mapping[str, object] | None = None
    media: TelegramMedia | None = None
    permalink: str | None = None

    def as_record(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "peer_id": self.peer_id,
            "message_id": self.message_id,
            "peer_channel_id": self.peer_channel_id,
            "username": self.username,
            "title": self.title,
            "text": self.text,
            "sent_at": self.sent_at,
            "edited_at": self.edited_at,
            "deleted_at": self.deleted_at,
            "sender_id": self.sender_id,
            "sender_username": self.sender_username,
            "sender_name": self.sender_name,
            "author": self.author,
            "entities": list(self.entities),
            "urls": list(self.urls),
            "reply_to_message_id": self.reply_to_message_id,
            "thread_id": self.thread_id,
            "forwarded_from": self.forwarded_from,
            "media": None if self.media is None else self.media.__dict__,
            "permalink": self.permalink,
        }


class TelegramGateway:
    def __init__(
        self,
        *,
        allowlist: TelegramAllowlist,
        store: TelegramStore,
        audit_log: TelegramAuditLog | None = None,
        memory_bridge: TelegramMemoryProposalBridge | None = None,
    ) -> None:
        self.allowlist = allowlist
        self.store = store
        self.audit_log = audit_log or TelegramAuditLog()
        self.memory_bridge = memory_bridge or TelegramMemoryProposalBridge()
        self.retrieval = TelegramRetrieval(store)

    @classmethod
    def for_sources(cls, sources: list[Mapping[str, object]], *, store_path: str | Path) -> "TelegramGateway":
        return cls(allowlist=TelegramAllowlist.from_iterable(sources), store=TelegramStore.open(store_path))

    def read_allowed_private(
        self,
        source_id: str,
        peer_id: str,
        *,
        limit: int | None = None,
        offset_id: int = 0,
        mtproto: TelegramMTProtoFacade | None = None,
    ) -> dict[str, object]:
        source = self.allowlist.source(source_id)
        facade = mtproto or TelegramMTProtoFacade(source)
        try:
            messages = facade.read_messages(peer_id, limit=limit, offset_id=offset_id)
        except TelegramFloodWaitError as exc:
            self.audit_log.record(
                action="READ_ALLOWED_PRIVATE",
                source_id=source_id,
                status="FLOOD_WAIT",
                reason_code="FLOOD_WAIT",
                details={"peer_id_hash": provenance_hash(source_id, peer_id), "seconds": exc.seconds, "count": 0},
            )
            return {"status": "FLOOD_WAIT", "reason_code": "FLOOD_WAIT", "retry_after_seconds": exc.seconds, "messages": []}
        except TelegramGatewayError as exc:
            self.audit_log.record(
                action="READ_ALLOWED_PRIVATE",
                source_id=source_id,
                status="BLOCKED",
                reason_code=exc.reason_code,
                details={"peer_id_hash": provenance_hash(source_id, peer_id), "count": 0},
            )
            return {"status": "BLOCKED", "reason_code": exc.reason_code, "messages": []}
        self.store.upsert_messages(messages)
        self.audit_log.record(action="READ_ALLOWED_PRIVATE", source_id=source_id, status="DONE", details={"count": len(messages), "peer_id_hash": provenance_hash(source_id, peer_id)})
        return {"status": "DONE", "messages": messages, "resume_offset": max((int(item["message_id"]) for item in messages), default=offset_id)}

    def resolve_source(self, source_ref: str, *, mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC) -> dict[str, object]:
        access_mode = TelegramAccessMode(mode)
        try:
            source = self.allowlist.resolve(source_ref, mode=access_mode)
            peer_id = source.require_public_ref(source_ref) if access_mode == TelegramAccessMode.READ_PUBLIC else source.resolve_private_peer(source_ref)
        except TelegramGatewayError as exc:
            source_id = source_ref if source_ref in self.allowlist.sources else "unknown"
            self.audit_log.record(action="RESOLVE_SOURCE", source_id=source_id, status="BLOCKED", reason_code=exc.reason_code, details={"count": 0})
            return {"status": "BLOCKED", "reason_code": exc.reason_code}
        result = _source_record(source, peer_id=peer_id)
        self.audit_log.record(action="RESOLVE_SOURCE", source_id=source.source_id, status="DONE", details={"count": 1, "result": "resolved"})
        return {"status": "DONE", "source": result}

    def get_history(
        self,
        source_ref: str,
        *,
        mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC,
        limit: int | None = None,
        offset_id: int = 0,
        min_date: str | None = None,
        max_date: str | None = None,
        mtproto: TelegramMTProtoFacade | None = None,
    ) -> dict[str, object]:
        resolved = self._resolve_peer(source_ref, TelegramAccessMode(mode))
        if resolved["status"] != "DONE":
            return resolved
        source = resolved["source_obj"]
        peer_id = str(resolved["peer_id"])
        facade = mtproto or TelegramMTProtoFacade(source)
        action = "READ_PUBLIC" if TelegramAccessMode(mode) == TelegramAccessMode.READ_PUBLIC else "READ_ALLOWED_PRIVATE"
        try:
            messages = facade.read_messages(peer_id, limit=limit, offset_id=offset_id, public=TelegramAccessMode(mode) == TelegramAccessMode.READ_PUBLIC)
        except TelegramFloodWaitError as exc:
            self.audit_log.record(action=action, source_id=source.source_id, status="FLOOD_WAIT", reason_code="FLOOD_WAIT", details={"seconds": exc.seconds, "count": 0, "peer_id_hash": provenance_hash(source.source_id, peer_id)})
            return {"status": "FLOOD_WAIT", "reason_code": "FLOOD_WAIT", "retry_after_seconds": exc.seconds, "messages": []}
        except TelegramGatewayError as exc:
            self.audit_log.record(action=action, source_id=source.source_id, status="BLOCKED", reason_code=exc.reason_code, details={"count": 0, "peer_id_hash": provenance_hash(source.source_id, peer_id)})
            return {"status": "BLOCKED", "reason_code": exc.reason_code, "messages": []}
        self.store.upsert_messages(messages)
        rows = self.store.get_history(source_id=source.source_id, peer_id=peer_id, limit=source.bounded_limit(limit), offset_id=offset_id, min_date=min_date, max_date=max_date)
        self.audit_log.record(action=action, source_id=source.source_id, status="DONE", details={"count": len(rows), "result": "history", "peer_id_hash": provenance_hash(source.source_id, peer_id)})
        return {"status": "DONE", "messages": rows, "resume_offset": max((int(item["message_id"]) for item in rows), default=offset_id)}

    def get_message(self, source_ref: str, message_id: int, *, mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC) -> dict[str, object]:
        resolved = self._resolve_peer(source_ref, TelegramAccessMode(mode))
        if resolved["status"] != "DONE":
            return resolved
        source = resolved["source_obj"]
        peer_id = str(resolved["peer_id"])
        row = self.store.get_message(source_id=source.source_id, peer_id=peer_id, message_id=message_id)
        status = "DONE" if row else "NOT_FOUND"
        self.audit_log.record(action="GET_MESSAGE", source_id=source.source_id, status=status, details={"count": 1 if row else 0, "peer_id_hash": provenance_hash(source.source_id, peer_id)})
        return {"status": status, "message": row}

    def search(
        self,
        query: str,
        *,
        source_ref: str | None = None,
        mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC,
        limit: int = 20,
        min_date: str | None = None,
        max_date: str | None = None,
        semantic: bool = False,
    ) -> dict[str, object]:
        source_id = None
        peer_id = None
        audit_source = "all"
        if source_ref is not None:
            resolved = self._resolve_peer(source_ref, TelegramAccessMode(mode))
            if resolved["status"] != "DONE":
                return resolved
            source = resolved["source_obj"]
            source_id = source.source_id
            peer_id = str(resolved["peer_id"])
            audit_source = source.source_id
            limit = source.bounded_limit(limit)
        rows = self.retrieval.search(query, limit=limit, semantic=semantic, source_id=source_id, peer_id=peer_id, min_date=min_date, max_date=max_date)
        self.audit_log.record(action="SEARCH", source_id=audit_source, status="DONE", details={"count": len(rows), "semantic": semantic})
        return {"status": "DONE", "messages": rows}

    def sync_source(
        self,
        source_ref: str,
        *,
        mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC,
        limit: int | None = None,
        overlap: int = 2,
        mtproto: TelegramMTProtoFacade | None = None,
    ) -> dict[str, object]:
        resolved = self._resolve_peer(source_ref, TelegramAccessMode(mode))
        if resolved["status"] != "DONE":
            return resolved
        source = resolved["source_obj"]
        peer_id = str(resolved["peer_id"])
        cursor = self.store.get_sync_cursor(source_id=source.source_id, peer_id=peer_id)
        min_id = max(0, int(cursor["cursor_message_id"]) - max(0, overlap)) if cursor else 0
        facade = mtproto or TelegramMTProtoFacade(source)
        access_mode = TelegramAccessMode(mode)
        try:
            messages = facade.read_messages(peer_id, limit=limit, min_id=min_id, public=access_mode == TelegramAccessMode.READ_PUBLIC)
        except TelegramFloodWaitError as exc:
            self.audit_log.record(action="SYNC_SOURCE", source_id=source.source_id, status="FLOOD_WAIT", reason_code="FLOOD_WAIT", details={"seconds": exc.seconds, "count": 0, "peer_id_hash": provenance_hash(source.source_id, peer_id)})
            return {"status": "FLOOD_WAIT", "reason_code": "FLOOD_WAIT", "retry_after_seconds": exc.seconds, "messages": []}
        except TelegramGatewayError as exc:
            self.audit_log.record(action="SYNC_SOURCE", source_id=source.source_id, status="BLOCKED", reason_code=exc.reason_code, details={"count": 0, "peer_id_hash": provenance_hash(source.source_id, peer_id)})
            return {"status": "BLOCKED", "reason_code": exc.reason_code, "messages": []}
        self.store.upsert_messages(messages)
        rows = [self.store.get_message(source_id=source.source_id, peer_id=peer_id, message_id=int(item["message_id"])) for item in messages]
        stored_messages = [row for row in rows if row is not None]
        next_cursor = max([int(item["message_id"]) for item in messages] + [int(cursor["cursor_message_id"]) if cursor else 0])
        self.store.save_sync_cursor(source_id=source.source_id, peer_id=peer_id, cursor_message_id=next_cursor, updated_at=_now(), state={"overlap": overlap})
        self.audit_log.record(action="SYNC_SOURCE", source_id=source.source_id, status="DONE", details={"count": len(stored_messages), "cursor_message_id": next_cursor, "peer_id_hash": provenance_hash(source.source_id, peer_id)})
        return {"status": "DONE", "count": len(stored_messages), "cursor_message_id": next_cursor, "messages": stored_messages}

    def watch_source(self, source_ref: str, *, mode: TelegramAccessMode | str = TelegramAccessMode.READ_PUBLIC, min_message_id: int = 0) -> dict[str, object]:
        resolved = self._resolve_peer(source_ref, TelegramAccessMode(mode))
        if resolved["status"] != "DONE":
            return resolved
        source = resolved["source_obj"]
        peer_id = str(resolved["peer_id"])
        watch = self.store.register_watch(source_id=source.source_id, peer_id=peer_id, min_message_id=min_message_id)
        self.audit_log.record(action="WATCH_SOURCE", source_id=source.source_id, status="DONE", details={"count": 1, "peer_id_hash": provenance_hash(source.source_id, peer_id)})
        return {"status": "DONE", "watch": watch}

    def list_allowed_sources(self) -> dict[str, object]:
        sources = [_source_record(source, peer_id=source.handle) for source in self.allowlist.sources.values()]
        self.audit_log.record(action="LIST_ALLOWED_SOURCES", source_id="all", status="DONE", details={"count": len(sources)})
        return {"status": "DONE", "sources": sources}

    def read_public_seed(self, source_id: str, messages: list[TelegramMessage]) -> dict[str, object]:
        source = self.allowlist.source(source_id)
        source.require(TelegramAccessMode.READ_PUBLIC)
        rows = [message.as_record() for message in messages]
        self.store.upsert_messages(rows)
        self.audit_log.record(action="READ_PUBLIC", source_id=source_id, status="DONE", details={"count": len(rows)})
        return {"status": "DONE", "count": len(rows)}

    def write_bot(self, source_id: str, message: str, *, env: Mapping[str, str] | None = None, opener: object | None = None) -> dict[str, object]:
        source = self.allowlist.source(source_id)
        source.require(TelegramAccessMode.WRITE_BOT)
        try:
            send_telegram_notification(message, env=env, opener=opener)  # type: ignore[arg-type]
        except TelegramNotificationError as exc:
            self.audit_log.record(action="WRITE_BOT", source_id=source_id, status="BLOCKED", reason_code="AUTH_REQUIRED")
            return {"status": "BLOCKED", "reason_code": "AUTH_REQUIRED", "error": type(exc).__name__}
        self.audit_log.record(action="WRITE_BOT", source_id=source_id, status="DONE")
        return {"status": "DONE"}

    def propose_memory_fact(self, message: Mapping[str, object], *, fact: str, recommendation: str | None = None) -> dict[str, object]:
        provenance = {
            "kind": "telegram_message_extract",
            "source_id": message.get("source_id"),
            "peer_id_hash": provenance_hash(message.get("source_id"), message.get("peer_id")),
            "message_id": message.get("message_id"),
            "source_evidence_hash": provenance_hash(message.get("source_id"), message.get("peer_id"), message.get("message_id"), fact),
        }
        return self.memory_bridge.propose(fact=fact, recommendation=recommendation, provenance=provenance)

    def run_public_integration_harness(self, source_id: str) -> dict[str, object]:
        source = self.allowlist.source(source_id)
        if source.handle != PUBLIC_INTEGRATION_SOURCE:
            raise TelegramGatewayError("INTEGRATION_SOURCE_MISMATCH", "integration source is not @midnightquantum")
        if "telegram_mtproto_string_session" not in source.secret_refs:
            return {"status": "AUTH_REQUIRED", "source": PUBLIC_INTEGRATION_SOURCE}
        return {"status": "BLOCKED", "reason_code": "LIVE_AUTHORIZATION_REQUIRED", "source": PUBLIC_INTEGRATION_SOURCE}

    def _resolve_peer(self, source_ref: str, mode: TelegramAccessMode) -> dict[str, Any]:
        try:
            source = self.allowlist.resolve(source_ref, mode=mode)
            peer_id = source.require_public_ref(source_ref) if mode == TelegramAccessMode.READ_PUBLIC else source.resolve_private_peer(source_ref)
        except TelegramGatewayError as exc:
            self.audit_log.record(action="RESOLVE_SOURCE", source_id=source_ref if source_ref in self.allowlist.sources else "unknown", status="BLOCKED", reason_code=exc.reason_code, details={"count": 0})
            return {"status": "BLOCKED", "reason_code": exc.reason_code}
        return {"status": "DONE", "source_obj": source, "peer_id": peer_id}


def _source_record(source: TelegramSource, *, peer_id: str) -> dict[str, object]:
    return {
        "source_id": source.source_id,
        "handle": source.handle,
        "peer_id": peer_id,
        "stable_peer_id": source.peer_id,
        "title": source.title,
        "access_modes": [mode.value for mode in source.access_modes],
        "max_page_size": source.max_page_size,
        "media_downloads_default": source.media_downloads_default,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
