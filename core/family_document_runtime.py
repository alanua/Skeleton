from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from core.family_document_intake import build_family_document_record, build_intake_request
from core.family_document_sources import LocalDirectoryDocumentSource
from core.private_memory_history import canonical_json, content_hash
from core.telegram_notifications import TelegramNotificationError, send_telegram_notification


FAMILY_DOCUMENT_RECEIPT_SCHEMA = "skeleton.family_document_receipt.v1"
FAMILY_DOCUMENT_RUNTIME_CONFIG_SCHEMA = "skeleton.family_document_runtime_config.v1"


class ArchiveSink(Protocol):
    def archive(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
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
                try:
                    sender(render_receipt_message(payload))
                except Exception as exc:
                    reason = type(exc).__name__
                    if isinstance(exc, TelegramNotificationError):
                        reason = str(exc) or reason
                    with connection:
                        connection.execute(
                            """
                            UPDATE family_document_receipts
                            SET state = 'RETRY', attempts = attempts + 1, last_error = ?
                            WHERE receipt_key = ? AND state != 'DONE'
                            """,
                            (reason[:160], row["receipt_key"]),
                        )
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

    def state_counts(self) -> dict[str, int]:
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) FROM family_document_receipts GROUP BY state"
            ).fetchall()
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
        completed = 0
        for document in self.source.scan():
            request = build_intake_request(document.path, source_id=document.source_id, source_sha256=document.sha256)
            classification = self.classifier(request.ocr_text) if self.classifier else None
            record = build_family_document_record(request, classification)
            archive_receipt = self.archive_sink.archive(record)
            self._enqueue_completed_receipts(record, archive_receipt)
            completed += 1
        drain_result = self.outbox.drain(sender=sender) if drain else {"sent": 0, "retry": 0}
        return {
            "schema": "skeleton.family_document_runtime_scan.v1",
            "completed_intakes": completed,
            "outbox": self.outbox.state_counts(),
            "drain": drain_result,
        }

    def _enqueue_completed_receipts(
        self,
        record: Mapping[str, Any],
        archive_receipt: Mapping[str, Any],
    ) -> None:
        intake_id = str(record["record_id"])
        base = {
            "schema": FAMILY_DOCUMENT_RECEIPT_SCHEMA,
            "intake_id": intake_id,
            "record_id": record["record_id"],
            "record_hash": record["record_hash"],
            "archive_status": archive_receipt.get("status"),
            "canonical_revision": archive_receipt.get("canonical_revision"),
        }
        self.outbox.enqueue({**base, "receipt_type": "intake", "receipt_key": f"intake:{intake_id}"})
        self.outbox.enqueue({**base, "receipt_type": "terminal", "receipt_key": f"terminal:{intake_id}", "status": "DONE"})


def render_receipt_message(receipt: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "Family document intake",
            f"Receipt: {receipt.get('receipt_type')}",
            f"Record: {receipt.get('record_id')}",
            f"Status: {receipt.get('status', receipt.get('archive_status'))}",
        ]
    )
