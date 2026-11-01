from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

from core.family_document_report import render_package_report
from core.intake_lifecycle import IntakeLifecycleStore
from core.private_memory_history import canonical_json, content_hash
from core.telegram_notifications import TelegramNotificationError, send_telegram_notification


class FamilyDocumentState(Protocol):
    """Noncanonical durable work/outbox state used by the document runtime."""

    def claim_work(self, source_id: str, source_sha256: str, **kwargs: Any) -> str | None: ...
    def begin_archive(self, source_id: str, source_sha256: str, token: str) -> None: ...
    def enqueue(self, receipt: Mapping[str, Any]) -> bool: ...
    def should_process(self, source_id: str, source_sha256: str, *, now: int | None = None) -> bool: ...
    def mark_work_done(self, source_id: str, source_sha256: str, *, token: str | None = None) -> None: ...
    def mark_work_failure(self, source_id: str, source_sha256: str, reason: str, *, token: str | None = None, now: int | None = None, max_attempts: int = 5, base_delay_seconds: int = 30) -> str: ...
    def work_state_counts(self) -> dict[str, int]: ...
    def record_lifecycle(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def pending_lifecycle_work(self, *, limit: int = 100) -> list[dict[str, Any]]: ...
    def drain(self, *, sender: Callable[[str], None] = send_telegram_notification, limit: int = 20) -> dict[str, object]: ...
    def state_counts(self) -> dict[str, int]: ...


class FamilyDocumentReceiptOutbox:
    """SQLite-backed *operational* state; canonical document truth stays in MemoryGateway."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.lifecycle = IntakeLifecycleStore(db_path)
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

    def record_lifecycle(self, **kwargs: Any) -> Mapping[str, Any]:
        item = self.lifecycle.record(item_kind="document", **kwargs)
        return item.public_mapping()

    def pending_lifecycle_work(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.lifecycle.pending_work(item_kind="document", limit=limit)

    def claim_work(self, source_id: str, source_sha256: str, *, now: int | None = None,
                   lease_seconds: int = 300) -> str | None:
        current = int(time.time()) if now is None else int(now)
        token = uuid.uuid4().hex
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT state, next_attempt_at, lease_until, attempts, archive_started FROM family_document_work WHERE source_id=? AND source_sha256=?",
                    (source_id, source_sha256),
                ).fetchone()
                if row is not None:
                    state, due, expiry, attempts, archive_started = row
                    if state == "CLAIMED" and expiry <= current:
                        attempts += 1
                        state = "REVIEW" if archive_started or attempts >= 5 else "RETRY"
                        connection.execute(
                            "UPDATE family_document_work SET state=?, attempts=?, next_attempt_at=?, claim_token=NULL, last_error='LEASE_EXPIRED' WHERE source_id=? AND source_sha256=?",
                            (state, attempts, current + min(30 * 2 ** min(attempts - 1, 7), 3600), source_id, source_sha256),
                        )
                        return None
                    if state != "RETRY" or due > current:
                        return None
                connection.execute(
                    """INSERT INTO family_document_work(source_id, source_sha256, state, attempts, claim_token, lease_until)
                    VALUES (?, ?, 'CLAIMED', 0, ?, ?)
                    ON CONFLICT(source_id, source_sha256) DO UPDATE SET state='CLAIMED', claim_token=excluded.claim_token, lease_until=excluded.lease_until""",
                    (source_id, source_sha256, token, current + max(1, lease_seconds)),
                )
        return token

    def begin_archive(self, source_id: str, source_sha256: str, token: str) -> None:
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            with connection:
                changed = connection.execute(
                    "UPDATE family_document_work SET archive_started=1 WHERE source_id=? AND source_sha256=? AND state='CLAIMED' AND claim_token=? AND lease_until>? AND archive_started=0",
                    (source_id, source_sha256, token, int(time.time())),
                ).rowcount
                if changed != 1:
                    raise RuntimeError("claim no longer owns archive permission")

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

    def mark_work_done(self, source_id: str, source_sha256: str, *, token: str | None = None) -> None:
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                owner = connection.execute(
                    "SELECT state, claim_token FROM family_document_work WHERE source_id=? AND source_sha256=?",
                    (source_id, source_sha256),
                ).fetchone()
                if token is not None:
                    if owner is None or owner != ("CLAIMED", token):
                        return
                elif owner and owner[0] in {"CLAIMED", "REVIEW", "DONE"}:
                    return
                connection.execute(
                    """
                    INSERT INTO family_document_work(source_id, source_sha256, state, attempts, next_attempt_at, last_error)
                    VALUES (?, ?, 'DONE', 0, 0, NULL)
                    ON CONFLICT(source_id, source_sha256) DO UPDATE SET
                        state='DONE', next_attempt_at=0, last_error=NULL, completed_at=CURRENT_TIMESTAMP
                    """,
                    (source_id, source_sha256),
                )
        self.record_lifecycle(
            source_ref=source_id,
            source_hash=source_sha256,
            state="DONE",
            blocker_reason="COMPLETED",
            next_action="none",
            provenance_refs=(f"document:{source_id}",),
            now=int(time.time()),
        )

    def mark_work_failure(
        self,
        source_id: str,
        source_sha256: str,
        reason: str,
        *,
        now: int | None = None,
        token: str | None = None,
        max_attempts: int = 5,
        base_delay_seconds: int = 30,
    ) -> str:
        current = int(time.time()) if now is None else int(now)
        safe_reason = "".join(character if character.isalnum() or character in "_.:-" else "_" for character in reason)[:96]
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                owner = connection.execute(
                    "SELECT state, claim_token, archive_started FROM family_document_work WHERE source_id=? AND source_sha256=?",
                    (source_id, source_sha256),
                ).fetchone()
                if token is not None and (owner is None or owner[:2] != ("CLAIMED", token)):
                    return str(owner[0]) if owner else "RETRY"
                if owner and (owner[0] in {"DONE", "REVIEW"} or (owner[0] == "CLAIMED" and owner[1] != token)):
                    return str(owner[0])
                row = connection.execute(
                    "SELECT attempts FROM family_document_work WHERE source_id = ? AND source_sha256 = ?",
                    (source_id, source_sha256),
                ).fetchone()
                attempts = (int(row[0]) if row is not None else 0) + 1
                state = "REVIEW" if attempts >= max_attempts or (owner and owner[2]) else "RETRY"
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
        lifecycle_state = "BLOCKED" if state == "REVIEW" else "DEFERRED"
        next_action = "operator_review_failed_document_intake" if state == "REVIEW" else "retry_document_intake_after_backoff"
        self.record_lifecycle(
            source_ref=source_id,
            source_hash=source_sha256,
            state=lifecycle_state,
            blocker_reason=safe_reason or "DOCUMENT_INTAKE_FAILED",
            next_action=next_action,
            provenance_refs=(f"document:{source_id}",),
            now=current,
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
            columns = {row[1] for row in connection.execute("PRAGMA table_info(family_document_work)")}
            for name, definition in (("claim_token", "TEXT"), ("lease_until", "INTEGER NOT NULL DEFAULT 0"), ("archive_started", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE family_document_work ADD COLUMN {name} {definition}")
            connection.commit()
        self.lifecycle.initialize()
        self.db_path.chmod(0o600)