from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
from typing import Any


MAIL_STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class StoredHandoff:
    handoff_ref: str
    message_hash: str
    classification: str
    case_ref: str | None
    correspondence_ref: str | None
    durable: bool


class MailRuntimeState:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_cursors (
                    provider_alias TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL,
                    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
                );
                CREATE TABLE IF NOT EXISTS message_handoffs (
                    message_hash TEXT PRIMARY KEY,
                    provider_alias TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    case_ref TEXT,
                    correspondence_ref TEXT,
                    handoff_ref TEXT NOT NULL UNIQUE,
                    durable INTEGER NOT NULL CHECK(durable IN (0, 1)),
                    receipt_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0)
                );
                CREATE TABLE IF NOT EXISTS scheduler_checkpoints (
                    schedule_id TEXT PRIMARY KEY,
                    message_hash TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0)
                );
                CREATE TABLE IF NOT EXISTS operator_packets (
                    packet_ref TEXT PRIMARY KEY,
                    message_hash TEXT NOT NULL,
                    packet_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0)
                );
                CREATE TABLE IF NOT EXISTS cleanup_receipts (
                    cleanup_ref TEXT PRIMARY KEY,
                    message_hash TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0)
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(MAIL_STATE_SCHEMA_VERSION),),
            )

    def get_cursor(self, provider_alias: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cursor FROM provider_cursors WHERE provider_alias = ?", (provider_alias,)
            ).fetchone()
            return None if row is None else str(row[0])

    def set_cursor(self, provider_alias: str, cursor: str, *, now: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_cursors(provider_alias, cursor, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(provider_alias) DO UPDATE SET
                    cursor = excluded.cursor,
                    updated_at = excluded.updated_at
                """,
                (provider_alias, cursor, now),
            )

    def get_handoff(self, message_hash: str) -> StoredHandoff | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT handoff_ref, message_hash, classification, case_ref,
                       correspondence_ref, durable
                  FROM message_handoffs
                 WHERE message_hash = ?
                """,
                (message_hash,),
            ).fetchone()
            if row is None:
                return None
            return StoredHandoff(
                handoff_ref=str(row[0]),
                message_hash=str(row[1]),
                classification=str(row[2]),
                case_ref=row[3],
                correspondence_ref=row[4],
                durable=bool(row[5]),
            )

    def record_handoff(
        self,
        *,
        message_hash: str,
        provider_alias: str,
        classification: str,
        case_ref: str | None,
        correspondence_ref: str | None,
        handoff_ref: str,
        receipt: Mapping[str, Any],
        now: int,
    ) -> tuple[StoredHandoff, bool]:
        receipt_json = json.dumps(receipt, ensure_ascii=True, allow_nan=False, sort_keys=True)
        with self._connect() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO message_handoffs(
                    message_hash, provider_alias, classification, case_ref,
                    correspondence_ref, handoff_ref, durable, receipt_json, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    message_hash,
                    provider_alias,
                    classification,
                    case_ref,
                    correspondence_ref,
                    handoff_ref,
                    receipt_json,
                    now,
                ),
            )
        existing = self.get_handoff(message_hash)
        assert existing is not None
        return existing, result.rowcount == 1

    def record_scheduler_checkpoint(
        self, *, schedule_id: str, message_hash: str, checkpoint: Mapping[str, Any], now: int
    ) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO scheduler_checkpoints(schedule_id, message_hash, checkpoint_json, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (
                    schedule_id,
                    message_hash,
                    json.dumps(checkpoint, ensure_ascii=True, allow_nan=False, sort_keys=True),
                    now,
                ),
            )
            return result.rowcount == 1

    def record_operator_packet(
        self, *, packet_ref: str, message_hash: str, packet: Mapping[str, Any], now: int
    ) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO operator_packets(packet_ref, message_hash, packet_json, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (
                    packet_ref,
                    message_hash,
                    json.dumps(packet, ensure_ascii=True, allow_nan=False, sort_keys=True),
                    now,
                ),
            )
            return result.rowcount == 1

    def record_cleanup(self, *, cleanup_ref: str, message_hash: str, action: str, now: int) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                "INSERT OR IGNORE INTO cleanup_receipts(cleanup_ref, message_hash, action, created_at) VALUES(?, ?, ?, ?)",
                (cleanup_ref, message_hash, action, now),
            )
            return result.rowcount == 1

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            return {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in (
                    "message_handoffs",
                    "scheduler_checkpoints",
                    "operator_packets",
                    "cleanup_receipts",
                )
            }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
