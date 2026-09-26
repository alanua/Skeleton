from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from core.telegram_audit import TelegramAuditLog, provenance_hash
from core.telegram_memory_bridge import TelegramMemoryProposalBridge
from core.telegram_mtproto import TelegramMTProtoFacade
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


@dataclass(frozen=True)
class TelegramMessage:
    source_id: str
    peer_id: str
    message_id: int
    text: str
    sent_at: str | None = None
    edited_at: str | None = None
    deleted_at: str | None = None
    media: TelegramMedia | None = None

    def as_record(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "peer_id": self.peer_id,
            "message_id": self.message_id,
            "text": self.text,
            "sent_at": self.sent_at,
            "edited_at": self.edited_at,
            "deleted_at": self.deleted_at,
            "media": None if self.media is None else self.media.__dict__,
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
        except TelegramGatewayError as exc:
            self.audit_log.record(action="READ_ALLOWED_PRIVATE", source_id=source_id, status="BLOCKED", reason_code=exc.reason_code)
            return {"status": "BLOCKED", "reason_code": exc.reason_code, "messages": []}
        self.store.upsert_messages(messages)
        self.audit_log.record(action="READ_ALLOWED_PRIVATE", source_id=source_id, status="DONE", details={"count": len(messages)})
        return {"status": "DONE", "messages": messages, "resume_offset": max((int(item["message_id"]) for item in messages), default=offset_id)}

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
