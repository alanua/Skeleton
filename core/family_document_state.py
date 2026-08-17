from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

from core.family_document_report import render_package_report
from core.private_memory_history import canonical_json, content_hash
from core.telegram_notifications import TelegramNotificationError, send_telegram_notification


class FamilyDocumentState(Protocol):
    """Noncanonical durable work/outbox state used by the document runtime."""

    def enqueue(self, receipt: Mapping[str, Any]) -> bool: ...
    def should_process(self, source_id: str, source_sha256: str, *, now: int | None = None) -> bool: ...
    def mark_work_done(self, source_id: str, source_sha256: str) -> None: ...
    def mark_work_failure(self, source_id: str, source_sha256: str, reason: str, *, now: int | None = None, max_attempts: int = 5, base_delay_seconds: int = 30) -> str: ...
    def work_state_counts(self) -> dict[str, int]: ...
    def drain(self, *, sender: Callable[[str], None] = send_telegram_notification, limit: int = 20) -> dict[str, object]: ...
    def state_counts(self) -> dict[str, int]: ...


class FamilyDocumentReceiptOutbox:
    """SQLite-backed *operational* state; canonical document truth stays in MemoryGateway."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._ensure_schema()

    def enqueue(self, receipt: Mapping[str, Any]) -> bool:
        receipt_key = str(receipt["receipt_key"])
        payload = canonical_json(receipt)
        payload_hash = content_hash(receipt)
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            with connection:
                row = connection.execute(
                    "SELECT payload_hash FROM family_document_receipts WHERE receipt_key = ?",
                    (receipt_key,),
                ).fetchone()
                if row is not None:
                    if str(row[0]) != payload_hash:
                        raise ValueError("receipt key reused with different payload")
                    return False
                connection.execute(
                    """
                    INSERT INTO family_document_receipts(receipt_key, state, payload_hash, payload_json, attempts)
                    VALUES (?, 'PENDING', ?, ?, 0)
                    """,
                    (receipt_key, payload_hash, payload),
                )
        return True

    def should_process(self, source_id: str, source_sha256: str, *, now: int | None = None) -> bool:
        current = int(time.time()) if now is None else int(now)
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            row = connection.execute(
                "SELECT state, next_attempt_at FROM family_document_work WHERE source_id = ? AND source_sha256 = ?",
                (source_id, source_sha256),
            ).fetchone()
        if row is None:
            return True
        state, next_attempt_at = str(row[0]), int(row[1] or 0)
        return state == "RETRY" and next_attempt_at <= current

    def mark_work_done(self, source_id: str, source_sha256: str) -> None:
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO family_document_work(source_id, source_sha256, state, attempts, next_attempt_at, last_error)
                    VALUES (?, ?, 'DONE', 0, 0, NULL)
                    ON CONFLICT(source_id, source_sha256) DO UPDATE SET
                        state='DONE', next_attempt_at=0, last_error=NULL, completed_at=CURRENT_TIMESTAMP
                    """,
                    (source_id, source_sha256),
                )

    def mark_work_failure(
        self,
        source_id: str,
        source_sha256: str,
        reason: str,
        *,
        now: int | None = None,
        max_attempts: int = 5,
        base_delay_seconds: int = 30,
    ) -> str:
        current = int(time.time()) if now is None else int(now)
        safe_reason = "".join(character if character.isalnum() or character in "_.:-" else "_" for character in reason)[:96]
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            with connection:
                row = connection.execute(
                    "SELECT attempts FROM family_document_work WHERE source_id = ? AND source_sha256 = ?",
                    (source_id, source_sha256),
                ).fetchone()
                attempts = (int(row[0]) if row is not None else 0) + 1
                state = "REVIEW" if attempts >= max_attempts else "RETRY"
                delay = 0 if state == "REVIEW" else min(base_delay_seconds * (2 ** (attempts - 1)), 3600)
                connection.execute(
                    """
                    INSERT INTO family_document_work(source_id, source_sha256, state, attempts, next_attempt_at, last_error)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, source_sha256) DO UPDATE SET
                        state=excluded.state,
                        attempts=excluded.attempts,
                        next_attempt_at=excluded.next_attempt_at,
                        last_error=excluded.last_error
                    """,
                    (source_id, source_sha256, state, attempts, current + delay, safe_reason),
                )
        return state

    def work_state_counts(self) -> dict[str, int]:
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            rows = connection.execute("SELECT state, COUNT(*) FROM family_document_work GROUP BY state").fetchall()
        return {str(state): int(count) for state, count in rows}

    def drain(
        self,
        *,
        sender: Callable[[str], None] = send_telegram_notification,
        limit: int = 20,
    ) -> dict[str, object]:
        sent = 0
        retry = 0
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT receipt_key, payload_json
                FROM family_document_receipts
                WHERE state IN ('PENDING', 'RETRY')
                ORDER BY created_at, receipt_key
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            for row in rows:
                payload = json.loads(str(row["payload_json"]))
                message = payload.get("message")
                if not isinstance(message, str) or not message.strip():
                    legacy_messages = render_package_report(payload.get("records", []))
                    if len(legacy_messages) != 1:
                        self._mark_retry(connection, str(row["receipt_key"]), "invalid_outbox_payload")
                        retry += 1
                        continue
                    message = legacy_messages[0]
                try:
                    sender(message)
                except Exception as exc:
                    reason = type(exc).__name__
                    if isinstance(exc, TelegramNotificationError):
                        reason = str(exc) or reason
                    self._mark_retry(connection, str(row["receipt_key"]), reason[:160])
                    retry += 1
                    continue
                with connection:
                    connection.execute(
                        """
                        UPDATE family_document_receipts
                        SET state = 'DONE', attempts = attempts + 1, last_error = NULL, completed_at = CURRENT_TIMESTAMP
                        WHERE receipt_key = ? AND state != 'DONE'
                        """,
                        (row["receipt_key"],),
                    )
                sent += 1
        return {"schema": "skeleton.family_document_outbox_drain.v1", "sent": sent, "retry": retry}

    @staticmethod
    def _mark_retry(connection: sqlite3.Connection, receipt_key: str, reason: str) -> None:
        with connection:
            connection.execute(
                """
                UPDATE family_document_receipts
                SET state = 'RETRY', attempts = attempts + 1, last_error = ?
                WHERE receipt_key = ? AND state != 'DONE'
                """,
                (reason, receipt_key),
            )

    def state_counts(self) -> dict[str, int]:
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            rows = connection.execute("SELECT state, COUNT(*) FROM family_document_receipts GROUP BY state").fetchall()
        return {str(state): int(count) for state, count in rows}

    def receipts(self) -> list[dict[str, object]]:
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT receipt_key, state, attempts, payload_json FROM family_document_receipts ORDER BY receipt_key"
            ).fetchall()
        return [
            {
                "receipt_key": str(row["receipt_key"]),
                "state": str(row["state"]),
                "attempts": int(row["attempts"]),
                "payload": json.loads(str(row["payload_json"])),
            }
            for row in rows
        ]

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS family_document_receipts (
                    receipt_key TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS family_document_work (
                    source_id TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    next_attempt_at INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT,
                    PRIMARY KEY(source_id, source_sha256)
                )
                """
            )
            connection.commit()
        self.db_path.chmod(0o600)
