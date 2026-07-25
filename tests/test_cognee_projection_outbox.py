from __future__ import annotations

import sqlite3
from pathlib import Path

from core.cognee_projection_outbox import (
    drain_projection_outbox,
    projection_outbox_status,
)
from core.memory_gateway_storage import (
    PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
    PrivateMemoryGatewayStorage,
)
from core.private_memory_stack import PrivateMemoryStack
from core.semantic_memory_projection import SemanticProjectionEvent, SemanticScope


class RecordingBackend:
    def __init__(self) -> None:
        self.events: list[SemanticProjectionEvent] = []
        self.forget_calls = 0

    def project(self, event: SemanticProjectionEvent) -> None:
        self.events.append(event)

    def forget_projection(self, scope: SemanticScope) -> int:
        assert scope == SemanticScope(project_id="skeleton", dataset_id="outbox_test")
        self.forget_calls += 1
        return len(self.events)


def _stack(tmp_path: Path) -> tuple[PrivateMemoryStack, PrivateMemoryGatewayStorage]:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    return stack, PrivateMemoryGatewayStorage(stack)


def _put(
    storage: PrivateMemoryGatewayStorage,
    *,
    fact_id: str,
    value: object,
    idempotency_key: str,
) -> dict[str, object]:
    return storage.execute_mutation(
        {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "operation": "put",
            "project_id": "skeleton",
            "dataset_id": "outbox_test",
            "fact_namespace": "skeleton.notes",
            "fact_id": fact_id,
            "value": value,
            "actor_ref": "test",
            "reason_code": "test-put",
            "approval_ref": "test-approval",
            "idempotency_key": idempotency_key,
        }
    )


def _delete(
    storage: PrivateMemoryGatewayStorage,
    *,
    fact_id: str,
    idempotency_key: str,
) -> dict[str, object]:
    return storage.execute_mutation(
        {
            "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
            "operation": "delete",
            "project_id": "skeleton",
            "dataset_id": "outbox_test",
            "fact_namespace": "skeleton.notes",
            "fact_id": fact_id,
            "actor_ref": "test",
            "reason_code": "test-delete",
            "approval_ref": "test-approval",
            "idempotency_key": idempotency_key,
        }
    )


def test_put_outbox_projects_exact_canonical_record_without_gateway_text_copy(
    tmp_path: Path,
) -> None:
    _, storage = _stack(tmp_path)
    receipt = _put(
        storage,
        fact_id="alpha",
        value={"summary": "distinctive synthetic private phrase"},
        idempotency_key="put-alpha",
    )
    backend = RecordingBackend()
    scope = SemanticScope(project_id="skeleton", dataset_id="outbox_test")

    result = drain_projection_outbox(tmp_path, scope, backend)

    assert result == {"claimed_count": 1, "projected_count": 1, "forgotten_count": 0}
    assert len(backend.events) == 1
    event = backend.events[0]
    assert event.canonical_ref == receipt["canonical_ref"]
    assert event.canonical_revision == receipt["canonical_revision"]
    assert event.content_hash
    assert "distinctive synthetic private phrase" in event.bounded_text
    assert projection_outbox_status(tmp_path, scope) == {
        "queued_count": 0,
        "processing_count": 0,
        "done_count": 1,
    }
    gateway_bytes = (tmp_path / "memory_gateway_mutations.sqlite").read_bytes()
    assert b"distinctive synthetic private phrase" not in gateway_bytes


def test_delete_forgets_scope_and_rebuilds_only_latest_active_refs(tmp_path: Path) -> None:
    _, storage = _stack(tmp_path)
    _put(storage, fact_id="alpha", value={"summary": "alpha"}, idempotency_key="put-alpha")
    _put(storage, fact_id="beta", value={"summary": "beta"}, idempotency_key="put-beta")
    backend = RecordingBackend()
    scope = SemanticScope(project_id="skeleton", dataset_id="outbox_test")
    drain_projection_outbox(tmp_path, scope, backend)
    backend.events.clear()

    delete_receipt = _delete(storage, fact_id="alpha", idempotency_key="delete-alpha")
    result = drain_projection_outbox(tmp_path, scope, backend)

    assert result["claimed_count"] == 1
    assert result["forgotten_count"] == 1
    assert backend.forget_calls == 1
    assert [event.canonical_ref for event in backend.events] == ["skeleton.notes:beta"]
    ledger = next((tmp_path / "cognee_runtime" / "receipts").glob("*.json")).read_text()
    assert f'"indexed_canonical_revision":{delete_receipt["canonical_revision"]}' in ledger


def test_processing_row_is_reclaimed_after_crash(tmp_path: Path) -> None:
    _, storage = _stack(tmp_path)
    _put(storage, fact_id="alpha", value={"summary": "alpha"}, idempotency_key="put-alpha")
    database = tmp_path / "memory_gateway_mutations.sqlite"
    with sqlite3.connect(str(database)) as connection:
        connection.execute(
            "UPDATE memory_gateway_projection_outbox SET state = 'PROCESSING'"
        )
        connection.commit()
    backend = RecordingBackend()
    scope = SemanticScope(project_id="skeleton", dataset_id="outbox_test")

    result = drain_projection_outbox(tmp_path, scope, backend)

    assert result["claimed_count"] == 1
    assert projection_outbox_status(tmp_path, scope)["done_count"] == 1
