from __future__ import annotations

import json

from core.family_document_sinks import (
    NOTIFICATION_DATASET_ID,
    MemoryGatewayNotificationSink,
    RecordingNotificationSink,
    build_intake_notification_record,
    build_terminal_notification_record,
)
from core.cognee_projection_outbox import projection_outbox_status
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack
from core.semantic_memory_projection import SemanticScope


def test_notification_ids_are_bound_to_canonical_identity() -> None:
    first = build_terminal_notification_record(
        canonical_document_id="doc-1",
        canonical_task_id="task-1",
        terminal_state="DONE",
    )
    replay = build_terminal_notification_record(
        canonical_document_id="doc-1",
        canonical_task_id="task-1",
        terminal_state="DONE",
    )
    other_state = build_terminal_notification_record(
        canonical_document_id="doc-1",
        canonical_task_id="task-1",
        terminal_state="REVIEW",
    )

    assert first["notification_id"] == replay["notification_id"]
    assert first["notification_id"] != other_state["notification_id"]
    rendered = json.dumps(first, sort_keys=True)
    assert "doc-1" not in rendered
    assert "task-1" not in rendered


def test_memory_gateway_sink_replay_does_not_duplicate_canonical_notification(tmp_path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    sink = MemoryGatewayNotificationSink(gateway)
    record = build_intake_notification_record(
        canonical_document_id="doc-accepted",
        stable_scan_id="stable-scan-1",
    )

    first = sink.enqueue(record)["payload"]
    second = sink.enqueue(record)["payload"]

    assert first["idempotency_classification"] == "NEW_MUTATION"
    assert second["idempotency_classification"] == "DUPLICATE_IDENTICAL"
    assert stack.status()["canonical_sqlite"]["active_fact_count"] == 1
    assert projection_outbox_status(
        tmp_path,
        SemanticScope(project_id="skeleton", dataset_id=NOTIFICATION_DATASET_ID),
    )["queued_count"] == 1


def test_recording_sink_deduplicates_without_private_content() -> None:
    sink = RecordingNotificationSink()
    record = build_intake_notification_record(
        canonical_document_id="private-doc-1",
        stable_scan_id="scan-from-mfp",
    )

    assert sink.enqueue(record)["idempotency_classification"] == "NEW_MUTATION"
    assert sink.enqueue(record)["idempotency_classification"] == "DUPLICATE_IDENTICAL"
    assert len(sink.records) == 1
    rendered = json.dumps(sink.records[0], sort_keys=True)
    assert "private-doc-1" not in rendered
    assert "scan-from-mfp" not in rendered
