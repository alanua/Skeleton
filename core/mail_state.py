from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping


class MailStateError(RuntimeError):
    pass


class MailStateStore:
    """Private local idempotency and cursor store for mail runtime state."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS account_cursors (
                    account_ref TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    cursor TEXT,
                    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
                );

                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_hash TEXT PRIMARY KEY,
                    account_ref TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_message_ref TEXT NOT NULL,
                    case_ref TEXT NOT NULL,
                    correspondence_ref TEXT NOT NULL,
                    important INTEGER NOT NULL CHECK(important IN (0, 1)),
                    deadline_at INTEGER,
                    processed_at INTEGER NOT NULL CHECK(processed_at >= 0),
                    receipt_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS scheduler_deadlines (
                    schedule_id TEXT PRIMARY KEY,
                    message_hash TEXT NOT NULL,
                    registered_at INTEGER NOT NULL CHECK(registered_at >= 0)
                );

                CREATE TABLE IF NOT EXISTS operator_packets (
                    packet_ref TEXT PRIMARY KEY,
                    message_hash TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0),
                    packet_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS private_routes (
                    route_ref TEXT PRIMARY KEY,
                    message_hash TEXT NOT NULL,
                    route_type TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    created_at INTEGER NOT NULL CHECK(created_at >= 0)
                );
                """
            )

    def get_cursor(self, account_ref: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cursor FROM account_cursors WHERE account_ref = ?", (account_ref,)
            ).fetchone()
            return None if row is None else row["cursor"]

    def set_cursor(self, account_ref: str, provider: str, cursor: str | None, *, now: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO account_cursors(account_ref, provider, cursor, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_ref) DO UPDATE SET
                    provider = excluded.provider,
                    cursor = excluded.cursor,
                    updated_at = excluded.updated_at
                """,
                (account_ref, provider, cursor, now),
            )

    def has_message(self, message_hash: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM processed_messages WHERE message_hash = ?", (message_hash,)
            ).fetchone()
            return row is not None

    def record_message(
        self,
        *,
        message_hash: str,
        account_ref: str,
        provider: str,
        provider_message_ref: str,
        case_ref: str,
        correspondence_ref: str,
        important: bool,
        deadline_at: int | None,
        processed_at: int,
        receipt: Mapping[str, Any],
    ) -> bool:
        with self._transaction() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO processed_messages(
                    message_hash, account_ref, provider, provider_message_ref, case_ref,
                    correspondence_ref, important, deadline_at, processed_at, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_hash,
                    account_ref,
                    provider,
                    provider_message_ref,
                    case_ref,
                    correspondence_ref,
                    int(important),
                    deadline_at,
                    processed_at,
                    _json(receipt),
                ),
            )
            return result.rowcount == 1

    def record_deadline(self, *, schedule_id: str, message_hash: str, now: int) -> bool:
        with self._transaction() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO scheduler_deadlines(schedule_id, message_hash, registered_at)
                VALUES (?, ?, ?)
                """,
                (schedule_id, message_hash, now),
            )
            return result.rowcount == 1

    def record_operator_packet(
        self, *, packet_ref: str, message_hash: str, created_at: int, packet: Mapping[str, Any]
    ) -> bool:
        with self._transaction() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO operator_packets(packet_ref, message_hash, created_at, packet_json)
                VALUES (?, ?, ?, ?)
                """,
                (packet_ref, message_hash, created_at, _json(packet)),
            )
            return result.rowcount == 1

    def record_private_route(
        self, *, route_ref: str, message_hash: str, route_type: str, target_ref: str, now: int
    ) -> bool:
        with self._transaction() as connection:
            result = connection.execute(
                """
                INSERT OR IGNORE INTO private_routes(route_ref, message_hash, route_type, target_ref, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (route_ref, message_hash, route_type, target_ref, now),
            )
            return result.rowcount == 1

    def aggregate_counts(self) -> dict[str, int]:
        with self._connect() as connection:
            return {
                "processed_messages": _count(connection, "processed_messages"),
                "scheduler_deadlines": _count(connection, "scheduler_deadlines"),
                "operator_packets": _count(connection, "operator_packets"),
                "private_routes": _count(connection, "private_routes"),
                "account_cursors": _count(connection, "account_cursors"),
            }

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"])


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)
