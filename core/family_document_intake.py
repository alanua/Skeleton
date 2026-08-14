from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.family_document_sinks import (
    FamilyDocumentNotificationSink,
    build_intake_notification_record,
)


INTAKE_RECEIPT_SCHEMA = "skeleton.family_document.intake_receipt.v1"


@dataclass(frozen=True)
class StableScan:
    stable_scan_id: str
    canonical_document_id: str
    accepted: bool


def accept_stable_scan(
    scan: StableScan | Mapping[str, object],
    *,
    notification_sink: FamilyDocumentNotificationSink,
) -> dict[str, object]:
    stable = _scan(scan)
    if not stable.accepted:
        return {
            "schema": INTAKE_RECEIPT_SCHEMA,
            "status": "PENDING_STABLE_GATE",
            "notification": "NOT_ENQUEUED",
        }
    record = build_intake_notification_record(
        canonical_document_id=stable.canonical_document_id,
        stable_scan_id=stable.stable_scan_id,
    )
    notification_receipt = notification_sink.enqueue(record)
    return {
        "schema": INTAKE_RECEIPT_SCHEMA,
        "status": "ACCEPTED",
        "notification": "QUEUED",
        "notification_receipt": notification_receipt,
    }


def _scan(value: StableScan | Mapping[str, object]) -> StableScan:
    if isinstance(value, StableScan):
        return value
    return StableScan(
        stable_scan_id=str(value.get("stable_scan_id", "")),
        canonical_document_id=str(value.get("canonical_document_id", "")),
        accepted=bool(value.get("accepted", False)),
    )
