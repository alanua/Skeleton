from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from core.family_document_intake import build_family_document_record, build_intake_request
from core.family_document_report import render_package_report
from core.family_document_sources import LocalDirectoryDocumentSource
from core.private_memory_history import canonical_json, content_hash
from core.telegram_notifications import TelegramNotificationError, send_telegram_notification


FAMILY_DOCUMENT_RECEIPT_SCHEMA = "skeleton.family_document_receipt.v1"
FAMILY_DOCUMENT_RUNTIME_CONFIG_SCHEMA = "skeleton.family_document_runtime_config.v1"


class ArchiveSink(Protocol):
    def archive(self, record: Mapping[str, Any], *, source_path: Path | None = None) -> Mapping[str, Any]:
        ...


class FamilyDocumentReceiptOutbox:
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


class FamilyDocumentRuntime:
    def __init__(
        self,
        *,
        source: LocalDirectoryDocumentSource,
        archive_sink: ArchiveSink,
        outbox: FamilyDocumentReceiptOutbox,
        classifier: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self.source = source
        self.archive_sink = archive_sink
        self.outbox = outbox
        self.classifier = classifier

    def scan_once(self, *, drain: bool = True, sender: Callable[[str], None] = send_telegram_notification) -> dict[str, object]:
        records: list[dict[str, object]] = []
        retrying = 0
        review_required = 0
        skipped = 0
        for document in self.source.scan():
            if not self.outbox.should_process(document.source_id, document.sha256):
                skipped += 1
                continue
            try:
                request = build_intake_request(document.path, source_id=document.source_id, source_sha256=document.sha256)
                classification = self.classifier(request.ocr_text) if self.classifier else {
                    "route": "REVIEW",
                    "reason_codes": ["CLASSIFIER_NOT_CONFIGURED"],
                }
                record = build_family_document_record(request, classification)
                archive_receipt = self.archive_sink.archive(record, source_path=document.path)
                report_record = dict(record)
                report_record["storage_label"] = archive_receipt.get("storage_label") or "Сімейний архів"
                records.append(report_record)
                self.outbox.mark_work_done(document.source_id, document.sha256)
            except Exception as exc:
                state = self.outbox.mark_work_failure(document.source_id, document.sha256, type(exc).__name__)
                if state == "REVIEW":
                    review_required += 1
                else:
                    retrying += 1
        if records:
            self._enqueue_package_report(records)
        drain_result = self.outbox.drain(sender=sender) if drain else {"sent": 0, "retry": 0}
        return {
            "schema": "skeleton.family_document_runtime_scan.v1",
            "completed_intakes": len(records),
            "retrying": retrying,
            "review_required": review_required,
            "skipped_completed_or_backoff": skipped,
            "work": self.outbox.work_state_counts(),
            "outbox": self.outbox.state_counts(),
            "drain": drain_result,
        }

    def _enqueue_package_report(self, records: list[Mapping[str, Any]]) -> None:
        identities = sorted(str(record["record_id"]) for record in records)
        package_key = content_hash({"records": identities})[:32]
        messages = render_package_report(records)
        for index, message in enumerate(messages, start=1):
            self.outbox.enqueue(
                {
                    "schema": FAMILY_DOCUMENT_RECEIPT_SCHEMA,
                    "receipt_type": "package_part",
                    "receipt_key": f"package:{package_key}:{index:04d}",
                    "package_key": package_key,
                    "part_index": index,
                    "part_count": len(messages),
                    "status": "DONE",
                    "message": message,
                }
            )
