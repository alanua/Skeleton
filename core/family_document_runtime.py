from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from core.family_document_intake import build_family_document_record, build_intake_request
from core.family_document_report import render_package_report
from core.family_document_sources import LocalDirectoryDocumentSource
from core.private_memory_history import canonical_json, content_hash
from core.telegram_notifications import TelegramNotificationError, send_telegram_notification


FAMILY_DOCUMENT_RECEIPT_SCHEMA = "skeleton.family_document_receipt.v1"
FAMILY_DOCUMENT_RUNTIME_CONFIG_SCHEMA = "skeleton.family_document_runtime_config.v1"


class ArchiveSink(Protocol):
    def archive(
        self,
        record: Mapping[str, Any],
        *,
        source_path: Path | None = None,
    ) -> Mapping[str, Any]:
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

    def mark_processed(self, *, source_sha256: str, record_id: str, record_hash: str) -> bool:
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            with connection:
                row = connection.execute(
                    """
                    SELECT record_id, record_hash
                    FROM family_document_processed
                    WHERE source_sha256 = ?
                    """,
                    (source_sha256,),
                ).fetchone()
                if row is not None:
                    if str(row[0]) != record_id or str(row[1]) != record_hash:
                        raise ValueError("processed source reused with different record")
                    return False
                connection.execute(
                    """
                    INSERT INTO family_document_processed(source_sha256, record_id, record_hash)
                    VALUES (?, ?, ?)
                    """,
                    (source_sha256, record_id, record_hash),
                )
        return True

    def is_processed(self, source_sha256: str) -> bool:
        if not source_sha256:
            return False
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            row = connection.execute(
                "SELECT 1 FROM family_document_processed WHERE source_sha256 = ?",
                (source_sha256,),
            ).fetchone()
        return row is not None

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
                message = _message_for_payload(payload)
                try:
                    sender(message)
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

    def processed_count(self) -> int:
        with closing(sqlite3.connect(str(self.db_path))) as connection:
            row = connection.execute("SELECT COUNT(*) FROM family_document_processed").fetchone()
        return int(row[0]) if row else 0

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
                CREATE TABLE IF NOT EXISTS family_document_processed (
                    source_sha256 TEXT PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

    def scan_once(
        self,
        *,
        drain: bool = True,
        sender: Callable[[str], None] = send_telegram_notification,
    ) -> dict[str, object]:
        completed = 0
        skipped_processed = 0
        report_records: list[dict[str, object]] = []
        for document in self.source.scan():
            if self.outbox.is_processed(document.sha256):
                skipped_processed += 1
                continue
            request = build_intake_request(
                document.path,
                source_id=document.source_id,
                source_sha256=document.sha256,
            )
            classification = self.classifier(request.ocr_text) if self.classifier else None
            record = build_family_document_record(request, classification)
            archive_receipt = self.archive_sink.archive(record, source_path=document.path)
            if archive_receipt.get("status") != "DONE":
                raise RuntimeError("family document archive did not complete")
            record_id = str(record["record_id"])
            record_hash = str(record["record_hash"])
            self.outbox.mark_processed(
                source_sha256=document.sha256,
                record_id=record_id,
                record_hash=record_hash,
            )
            report_record = dict(record)
            classification_for_report = dict(record.get("classification", {}))
            archive_label = archive_receipt.get("archive_label")
            if isinstance(archive_label, str) and archive_label:
                classification_for_report.setdefault("storage_label", archive_label)
            report_record["classification"] = classification_for_report
            report_records.append(report_record)
            completed += 1

        reports_enqueued = 0
        if report_records:
            package_key = _package_key(report_records)
            for index, message in enumerate(render_package_report(report_records), start=1):
                if self.outbox.enqueue(
                    {
                        "schema": FAMILY_DOCUMENT_RECEIPT_SCHEMA,
                        "receipt_key": f"package:{package_key}:{index}",
                        "receipt_type": "package_report",
                        "status": "DONE",
                        "message": message,
                        "part": index,
                    }
                ):
                    reports_enqueued += 1

        drain_result = self.outbox.drain(sender=sender) if drain else {"sent": 0, "retry": 0}
        return {
            "schema": "skeleton.family_document_runtime_scan.v1",
            "completed_intakes": completed,
            "skipped_processed": skipped_processed,
            "processed_total": self.outbox.processed_count(),
            "reports_enqueued": reports_enqueued,
            "outbox": self.outbox.state_counts(),
            "drain": drain_result,
        }


def render_receipt_message(receipt: Mapping[str, Any]) -> str:
    """Legacy fallback for old durable receipt rows; new reports use package_report."""
    receipt_type = str(receipt.get("receipt_type") or "receipt")
    status = str(receipt.get("status", receipt.get("archive_status", "UNKNOWN")))
    if receipt_type == "terminal":
        return f"Сканування завершено\nСтатус: {status}"
    if receipt_type == "intake":
        return "Сканування прийнято"
    return f"Сканування\nСтатус: {status}"


def _message_for_payload(payload: Mapping[str, Any]) -> str:
    if payload.get("receipt_type") == "package_report":
        message = payload.get("message")
        if not isinstance(message, str) or not message or len(message) > 4096:
            raise ValueError("package report message invalid")
        return message
    return render_receipt_message(payload)


def _package_key(records: Sequence[Mapping[str, Any]]) -> str:
    parts = [f"{record.get('record_id')}:{record.get('record_hash')}" for record in records]
    return hashlib.sha256("\x1f".join(sorted(parts)).encode("utf-8")).hexdigest()[:32]
