from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from core.family_document_calendar import (
    FamilyDocumentCalendar,
    FamilyDocumentCalendarReceipt,
    NoopFamilyDocumentCalendar,
)
from core.family_document_intake import build_family_document_record, build_intake_request
from core.family_document_report import render_package_report
from core.family_document_sources import LocalDirectoryDocumentSource
from core.family_document_state import FamilyDocumentReceiptOutbox, FamilyDocumentState
from core.private_memory_history import content_hash
from core.telegram_notifications import send_telegram_notification


FAMILY_DOCUMENT_RECEIPT_SCHEMA = "skeleton.family_document_receipt.v1"
FAMILY_DOCUMENT_RUNTIME_CONFIG_SCHEMA = "skeleton.family_document_runtime_config.v1"


class ArchiveSink(Protocol):
    def archive(self, record: Mapping[str, Any], *, source_path: Path | None = None) -> Mapping[str, Any]:
        ...


class FamilyDocumentRuntime:
    def __init__(
        self,
        *,
        source: LocalDirectoryDocumentSource,
        archive_sink: ArchiveSink,
        outbox: FamilyDocumentState,
        classifier: Callable[[str], Mapping[str, Any]] | None = None,
        calendar: FamilyDocumentCalendar | None = None,
    ) -> None:
        self.source = source
        self.archive_sink = archive_sink
        self.state = outbox
        self.outbox = outbox  # compatibility alias; state remains explicitly noncanonical
        self.classifier = classifier
        self.calendar = calendar or NoopFamilyDocumentCalendar()

    def scan_once(
        self,
        *,
        drain: bool = True,
        sender: Callable[[str], None] = send_telegram_notification,
    ) -> dict[str, object]:
        records: list[dict[str, object]] = []
        retrying = 0
        review_required = 0
        skipped = 0
        for document in self.source.scan():
            if not self.state.should_process(document.source_id, document.sha256):
                skipped += 1
                continue
            try:
                request = build_intake_request(
                    document.path,
                    source_id=document.source_id,
                    source_sha256=document.sha256,
                )
                classification = self.classifier(request.ocr_text) if self.classifier else {
                    "route": "REVIEW",
                    "reason_codes": ["CLASSIFIER_NOT_CONFIGURED"],
                    "event_candidates": [],
                }
                record = build_family_document_record(request, classification)
                archive_receipt = self.archive_sink.archive(record, source_path=document.path)
                _require_authoritative_archive_receipt(archive_receipt)
                calendar_receipt = self.calendar.upsert(record)
                _require_calendar_receipt(calendar_receipt)
                report_record = dict(record)
                report_record["storage_label"] = archive_receipt.get("storage_label") or "Сімейний архів"
                report_record["completion_evidence"] = _completion_evidence(
                    record,
                    archive_receipt,
                    calendar_receipt,
                )
                records.append(report_record)
                self.state.mark_work_done(document.source_id, document.sha256)
            except Exception as exc:
                state = self.state.mark_work_failure(
                    document.source_id,
                    document.sha256,
                    type(exc).__name__,
                )
                if state == "REVIEW":
                    review_required += 1
                else:
                    retrying += 1
        if records:
            self._enqueue_package_report(records)
        drain_result = self.state.drain(sender=sender) if drain else {"sent": 0, "retry": 0}
        return {
            "schema": "skeleton.family_document_runtime_scan.v1",
            "completed_intakes": len(records),
            "retrying": retrying,
            "review_required": review_required,
            "skipped_completed_or_backoff": skipped,
            "work": self.state.work_state_counts(),
            "outbox": self.state.state_counts(),
            "drain": drain_result,
        }

    def _enqueue_package_report(self, records: list[Mapping[str, Any]]) -> None:
        identities = sorted(str(record["record_id"]) for record in records)
        package_key = content_hash({"records": identities})[:32]
        messages = render_package_report(records)
        for index, message in enumerate(messages, start=1):
            self.state.enqueue(
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


def _require_authoritative_archive_receipt(receipt: Mapping[str, Any]) -> None:
    if receipt.get("status") != "DONE":
        raise RuntimeError("archive commit not complete")
    if "original_readback_verified" in receipt and receipt.get("original_readback_verified") is not True:
        raise RuntimeError("original archive readback not verified")
    if "canonical_readback_verified" in receipt and receipt.get("canonical_readback_verified") is not True:
        raise RuntimeError("MemoryGateway exact readback not verified")


def _require_calendar_receipt(receipt: FamilyDocumentCalendarReceipt) -> None:
    if receipt.status not in {"DONE", "NO_EVENT"}:
        raise RuntimeError("calendar upsert not complete")
    if receipt.required and receipt.event_count <= 0:
        raise RuntimeError("calendar receipt malformed")


def _completion_evidence(
    record: Mapping[str, Any],
    archive: Mapping[str, Any],
    calendar: FamilyDocumentCalendarReceipt,
) -> dict[str, object]:
    return {
        "record_id": str(record.get("record_id") or ""),
        "source_sha256": str(record.get("source_sha256") or ""),
        "archive_state": str(archive.get("archive_state") or "canonical_only"),
        "original_sha256": str(archive.get("original_sha256") or record.get("source_sha256") or ""),
        "original_readback_verified": archive.get("original_readback_verified") is True,
        "canonical_ref": str(archive.get("canonical_ref") or ""),
        "canonical_revision": archive.get("canonical_revision"),
        "canonical_readback_verified": archive.get("canonical_readback_verified") is True,
        "calendar_status": calendar.status,
        "calendar_required": calendar.required,
        "calendar_event_count": calendar.event_count,
        "calendar_semantic_hashes": list(calendar.semantic_hashes),
    }


__all__ = [
    "FamilyDocumentReceiptOutbox",
    "FamilyDocumentRuntime",
    "FAMILY_DOCUMENT_RECEIPT_SCHEMA",
    "FAMILY_DOCUMENT_RUNTIME_CONFIG_SCHEMA",
]
