from __future__ import annotations

from core.family_document_intake import StableScan, accept_stable_scan
from core.family_document_sinks import RecordingNotificationSink


def test_no_notification_before_stable_file_gate() -> None:
    sink = RecordingNotificationSink()

    receipt = accept_stable_scan(
        StableScan(
            stable_scan_id="scan-not-stable",
            canonical_document_id="doc-not-stable",
            accepted=False,
        ),
        notification_sink=sink,
    )

    assert receipt["status"] == "PENDING_STABLE_GATE"
    assert receipt["notification"] == "NOT_ENQUEUED"
    assert sink.records == []


def test_stable_accepted_scan_enqueues_one_intake_receipt_on_replay() -> None:
    sink = RecordingNotificationSink()
    scan = StableScan(
        stable_scan_id="scan-stable",
        canonical_document_id="doc-stable",
        accepted=True,
    )

    first = accept_stable_scan(scan, notification_sink=sink)
    second = accept_stable_scan(scan, notification_sink=sink)

    assert first["notification"] == "QUEUED"
    assert second["notification"] == "QUEUED"
    assert len(sink.records) == 1
    assert sink.records[0]["receipt_type"] == "RECEIVED"
