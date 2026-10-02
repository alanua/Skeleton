from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any

from core.intake_lifecycle import IntakeLifecycleStore, stable_intake_id


@dataclass(frozen=True)
class MailMessageState:
    message_hash: str
    account_ref: str
    status: str
    attempts: int
    first_seen_at: int
    updated_at: int
    last_reason: str


class MailStateStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.lifecycle = IntakeLifecycleStore(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_cursors (
                    account_ref TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    cursor_ref TEXT,
                    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
                );

                CREATE TABLE IF NOT EXISTS message_states (
                    message_hash TEXT PRIMARY KEY,
                    account_ref TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL CHECK(attempts >= 0),
                    first_seen_at INTEGER NOT NULL CHECK(first_seen_at >= 0),
                    updated_at INTEGER NOT NULL CHECK(updated_at >= 0),
                    last_reason TEXT NOT NULL
                );
                """
            )
        self.lifecycle.initialize()

    def get_cursor(self, account_ref: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cursor_ref FROM provider_cursors WHERE account_ref = ?",
                (account_ref,),
            ).fetchone()
            return None if row is None else row[0]

    def update_cursor(
        self, *, account_ref: str, provider: str, cursor_ref: str | None, now: int
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_cursors(account_ref, provider, cursor_ref, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_ref) DO UPDATE SET
                    provider = excluded.provider,
                    cursor_ref = excluded.cursor_ref,
                    updated_at = excluded.updated_at
                """,
                (account_ref, provider, cursor_ref, now),
            )

    def claim_message(
        self,
        *,
        message_hash: str,
        account_ref: str,
        now: int,
        max_attempts: int,
    ) -> tuple[MailMessageState, bool]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM message_states WHERE message_hash = ?",
                (message_hash,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO message_states(
                        message_hash, account_ref, status, attempts,
                        first_seen_at, updated_at, last_reason
                    ) VALUES (?, ?, 'processing', 1, ?, ?, 'PROCESSING')
                    """,
                    (message_hash, account_ref, now, now),
                )
                return (
                    MailMessageState(message_hash, account_ref, "processing", 1, now, now, "PROCESSING"),
                    True,
                )
            state = self._message_state(row)
            if state.status in {"done", "ignored", "needs_operator"}:
                return state, False
            if state.attempts >= max_attempts:
                self.mark_message(
                    message_hash=message_hash,
                    status="needs_operator",
                    reason="MAIL_RETRY_LIMIT_EXHAUSTED",
                    now=now,
                )
                return (
                    MailMessageState(
                        message_hash,
                        account_ref,
                        "needs_operator",
                        state.attempts,
                        state.first_seen_at,
                        now,
                        "MAIL_RETRY_LIMIT_EXHAUSTED",
                    ),
                    False,
                )
            connection.execute(
                """
                UPDATE message_states
                   SET status = 'processing',
                       attempts = attempts + 1,
                       updated_at = ?,
                       last_reason = 'PROCESSING'
                 WHERE message_hash = ?
                """,
                (now, message_hash),
            )
            return (
                MailMessageState(
                    message_hash,
                    account_ref,
                    "processing",
                    state.attempts + 1,
                    state.first_seen_at,
                    now,
                    "PROCESSING",
                ),
                True,
            )

    def record_lifecycle(
        self,
        *,
        message_hash: str,
        account_ref: str,
        state: str,
        blocker_reason: str,
        next_action: str,
        provenance_refs: tuple[str, ...] = (),
        canonical_ref: str | None = None,
        record_ref: str | None = None,
        now: int,
    ) -> dict[str, Any]:
        item = self.lifecycle.record(
            item_kind="mail",
            source_ref=account_ref,
            source_hash=message_hash,
            state=state,
            blocker_reason=blocker_reason,
            next_action=next_action,
            provenance_refs=provenance_refs or (f"mail:{message_hash[:24]}",),
            canonical_ref=canonical_ref,
            record_ref=record_ref,
            now=now,
        )
        return item.public_mapping()

    def record_account_blocked(
        self,
        *,
        account_ref: str,
        reason: str,
        next_action: str,
        now: int,
    ) -> dict[str, Any]:
        import hashlib

        digest = hashlib.sha256(account_ref.encode("utf-8")).hexdigest()
        item = self.lifecycle.record(
            item_kind="mail",
            source_ref=account_ref,
            source_hash=digest,
            state="BLOCKED",
            blocker_reason=reason,
            next_action=next_action,
            provenance_refs=(f"mail-account:{digest[:24]}",),
            now=now,
        )
        return item.public_mapping()

    def record_account_resumed(self, *, account_ref: str, now: int) -> dict[str, Any] | None:
        import hashlib

        digest = hashlib.sha256(account_ref.encode("utf-8")).hexdigest()
        intake_id = stable_intake_id(item_kind="mail", source_ref=account_ref, source_hash=digest)
        if self.lifecycle.get(intake_id) is None:
            return None
        item = self.lifecycle.record(
            item_kind="mail",
            source_ref=account_ref,
            source_hash=digest,
            state="DONE",
            blocker_reason="COMPLETED",
            next_action="none",
            provenance_refs=(f"mail-account:{digest[:24]}",),
            now=now,
        )
        return item.public_mapping()

    def pending_lifecycle_work(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.lifecycle.pending_work(item_kind="mail", limit=limit)

    def mark_message(self, *, message_hash: str, status: str, reason: str, now: int) -> None:
        if status not in {"done", "ignored", "failed", "needs_operator"}:
            raise ValueError("invalid mail message status")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE message_states
                   SET status = ?, updated_at = ?, last_reason = ?
                 WHERE message_hash = ?
                """,
                (status, now, reason, message_hash),
            )
        lifecycle_state = {
            "done": "DONE",
            "ignored": "DONE",
            "failed": "DEFERRED",
            "needs_operator": "BLOCKED",
        }[status]
        next_action = {
            "done": "none",
            "ignored": "none",
            "failed": "resume_mail_processing_after_backoff",
            "needs_operator": "operator_review_action_required_mail",
        }[status]
        self.record_lifecycle(
            message_hash=message_hash,
            account_ref=self.get_message(message_hash).account_ref if self.get_message(message_hash) else "acct:unknown",
            state=lifecycle_state,
            blocker_reason=reason,
            next_action=next_action,
            now=now,
        )

    def get_message(self, message_hash: str) -> MailMessageState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM message_states WHERE message_hash = ?",
                (message_hash,),
            ).fetchone()
            return None if row is None else self._message_state(row)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _message_state(row: Any) -> MailMessageState:
        return MailMessageState(
            message_hash=str(row["message_hash"]),
            account_ref=str(row["account_ref"]),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            first_seen_at=int(row["first_seen_at"]),
            updated_at=int(row["updated_at"]),
            last_reason=str(row["last_reason"]),
        )
