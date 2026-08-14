from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from core.family_document_sinks import (
    FamilyDocumentNotificationError,
    FamilyDocumentNotificationSink,
    build_terminal_notification_record,
    terminal_receipt_type,
)


RUNTIME_RECEIPT_SCHEMA = "skeleton.family_document.runtime_receipt.v1"


class CanonicalCommit(Protocol):
    def __call__(self) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class CanonicalProcessingResult:
    canonical_document_id: str
    canonical_task_id: str
    state: str


def complete_canonical_processing(
    result: CanonicalProcessingResult | Mapping[str, object],
    *,
    commit: CanonicalCommit,
    notification_sink: FamilyDocumentNotificationSink,
) -> dict[str, object]:
    processing = _result(result)
    receipt_type = terminal_receipt_type(processing.state)
    commit_receipt = dict(commit())
    record = build_terminal_notification_record(
        canonical_document_id=processing.canonical_document_id,
        canonical_task_id=processing.canonical_task_id,
        terminal_state=receipt_type,
    )
    try:
        notification_receipt = notification_sink.enqueue(record)
        notification_status = "QUEUED"
    except FamilyDocumentNotificationError as exc:
        notification_receipt = {
            "status": "RETRYABLE",
            "reason_code": str(exc),
            "notification_id": record["notification_id"],
        }
        notification_status = "RETRYABLE"
    return {
        "schema": RUNTIME_RECEIPT_SCHEMA,
        "status": receipt_type,
        "canonical_commit": commit_receipt,
        "notification": notification_status,
        "notification_receipt": notification_receipt,
    }


def _result(value: CanonicalProcessingResult | Mapping[str, object]) -> CanonicalProcessingResult:
    if isinstance(value, CanonicalProcessingResult):
        return value
    return CanonicalProcessingResult(
        canonical_document_id=str(value.get("canonical_document_id", "")),
        canonical_task_id=str(value.get("canonical_task_id", "")),
        state=str(value.get("state", "")),
    )
