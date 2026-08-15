from __future__ import annotations

import pytest

from core.family_document_runtime import CanonicalProcessingResult, complete_canonical_processing
from core.family_document_sinks import RecordingNotificationSink


@pytest.mark.parametrize(
    ("state", "receipt_type"),
    [
        ("DONE", "DONE"),
        ("AMBIGUOUS", "REVIEW"),
        ("RETRYABLE", "RETRY"),
        ("FAILED", "FAILED"),
        ("QUARANTINE", "QUARANTINED"),
    ],
)
def test_terminal_processing_enqueues_bounded_receipt_types(state: str, receipt_type: str) -> None:
    sink = RecordingNotificationSink()

    receipt = complete_canonical_processing(
        CanonicalProcessingResult(
            canonical_document_id="doc-terminal",
            canonical_task_id="task-terminal",
            state=state,
        ),
        commit=lambda: {"status": "DONE", "canonical_revision": 7},
        notification_sink=sink,
    )

    assert receipt["status"] == receipt_type
    assert receipt["notification"] == "QUEUED"
    assert len(sink.records) == 1
    assert sink.records[0]["receipt_type"] == receipt_type


def test_terminal_replay_does_not_duplicate_notification() -> None:
    sink = RecordingNotificationSink()
    result = CanonicalProcessingResult(
        canonical_document_id="doc-replay",
        canonical_task_id="task-replay",
        state="DONE",
    )

    complete_canonical_processing(result, commit=lambda: {"status": "DONE"}, notification_sink=sink)
    complete_canonical_processing(result, commit=lambda: {"status": "DONE"}, notification_sink=sink)

    assert len(sink.records) == 1


def test_notification_delivery_failure_does_not_rollback_canonical_commit() -> None:
    committed: list[str] = []
    sink = RecordingNotificationSink(fail_after_record=True)

    receipt = complete_canonical_processing(
        CanonicalProcessingResult(
            canonical_document_id="doc-committed",
            canonical_task_id="task-committed",
            state="DONE",
        ),
        commit=lambda: committed.append("canonical-memory-gateway-commit") or {"status": "DONE"},
        notification_sink=sink,
    )

    assert committed == ["canonical-memory-gateway-commit"]
    assert receipt["notification"] == "RETRYABLE"
    assert receipt["notification_receipt"]["status"] == "RETRYABLE"
    assert len(sink.records) == 1
