from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack
from core.project_memory_adapter import (
    PROJECT_MEMORY_REQUEST_SCHEMA,
    ProjectMemoryAdapterError,
    ProjectMemoryGatewayAdapter,
    project_memory_binding,
)


def request(operation: str, memory_class: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": PROJECT_MEMORY_REQUEST_SCHEMA,
        "operation": operation,
        "project_id": "skeleton",
        "dataset_id": "synthetic",
        "memory_class": memory_class,
        "key": "primary_fact",
        "value": {"summary": "synthetic project memory value"},
        "idempotency_key": f"idem-{operation}-{memory_class.lower()}",
        "reason_code": operation,
    }
    payload.update(overrides)
    return payload


def public_adapter() -> ProjectMemoryGatewayAdapter:
    return ProjectMemoryGatewayAdapter(
        MemoryGateway(capability_token(namespaces=("skeleton",), public_mode=True)),
        project_memory_binding(namespace="skeleton", project_id="skeleton"),
    )


def private_adapter(tmp_path: Path) -> ProjectMemoryGatewayAdapter:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    return ProjectMemoryGatewayAdapter(
        gateway,
        project_memory_binding(
            namespace="skeleton",
            project_id="skeleton",
            dataset_id="synthetic",
            capability_class="PRIVATE_RUNTIME_ONLY",
        ),
    )


def test_public_boundaries_block_private_before_gateway_mutation(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    before = stack.status()["canonical_sqlite"]["canonical_revision"]
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=True),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    adapter = ProjectMemoryGatewayAdapter(
        gateway,
        project_memory_binding(namespace="skeleton", project_id="skeleton"),
    )
    private_request = request(
        "propose_fact",
        "PRIVATE",
        key="blocked_private_fact",
        capability_class="PUBLIC_SAFE",
    )

    with pytest.raises(ProjectMemoryAdapterError) as excinfo:
        adapter.propose_fact(private_request)

    assert excinfo.value.reason_code == "PRIVATE_BINDING_REQUIRED"
    assert stack.status()["canonical_sqlite"]["canonical_revision"] == before
    assert stack.status()["canonical_sqlite"]["active_fact_count"] == 0


def test_private_runtime_only_roundtrip_reaches_gateway_and_reads_back_exact(tmp_path: Path) -> None:
    adapter = private_adapter(tmp_path)
    private_request = request(
        "propose_fact",
        "PRIVATE",
        key="private_roundtrip",
        capability_class="PRIVATE_RUNTIME_ONLY",
        value={"summary": "exact private roundtrip"},
        idempotency_key="idem-private-roundtrip",
    )

    receipt = adapter.propose_fact(private_request)
    readback = adapter.read_context(
        request(
            "read_context",
            "PRIVATE",
            key="project_memory.fact:private_roundtrip",
            canonical_ref="project_memory.fact:private_roundtrip",
            capability_class="PRIVATE_RUNTIME_ONLY",
            idempotency_key="idem-private-readback",
        )
    )

    assert receipt["payload"]["operation"] == "put"
    assert receipt["payload"]["canonical_ref"] == "project_memory.fact:private_roundtrip"
    assert "exact private roundtrip" not in json.dumps(receipt, sort_keys=True)
    assert readback["payload"]["authoritative"] is True
    assert readback["payload"]["canonical_ref"] == "project_memory.fact:private_roundtrip"
    assert readback["payload"]["value"]["summary"] == "exact private roundtrip"


def test_private_request_requires_private_gateway_capability(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=True),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    adapter = ProjectMemoryGatewayAdapter(
        gateway,
        project_memory_binding(
            namespace="skeleton",
            project_id="skeleton",
            capability_class="PRIVATE_RUNTIME_ONLY",
        ),
    )

    with pytest.raises(ProjectMemoryAdapterError) as excinfo:
        adapter.propose_fact(
            request(
                "propose_fact",
                "PRIVATE",
                key="blocked_by_gateway",
                capability_class="PRIVATE_RUNTIME_ONLY",
            )
        )

    assert excinfo.value.reason_code == "PRIVATE_GATEWAY_CAPABILITY_REQUIRED"
    assert stack.status()["canonical_sqlite"]["active_fact_count"] == 0


def test_canon_preference_remains_pending_operator_review() -> None:
    receipt = public_adapter().propose_preference(
        request(
            "propose_preference",
            "CANON",
            key="primary_fact",
            value={"state": "proposed-not-written"},
            idempotency_key="idem-canon-preference",
        )
    )
    payload = receipt["payload"]

    assert payload["proposal_event"]["status"] == "ACCEPTED"
    assert payload["canonical_write_performed"] is False
    assert payload["operator_approval_required"] is True
    exact = public_adapter().read_context(request("read_context", "CANON", key="primary_fact"))
    assert exact["payload"]["canonical_ref"] == "canon-skeleton-skeleton-primary"
    assert "proposed-not-written" not in json.dumps([payload, exact], sort_keys=True)


def test_non_canon_public_classes_are_review_receipts_without_values() -> None:
    adapter = public_adapter()
    for memory_class in ("REVIEW", "BACKLOG", "REJECTED", "TEMPORARY"):
        receipt = adapter.propose_task(
            request(
                "propose_task",
                memory_class,
                key=f"{memory_class.lower()}_task",
                value={"summary": f"{memory_class} raw value"},
            )
        )
        assert receipt["payload"]["canonical_write_performed"] is False
        assert f"{memory_class} raw value" not in json.dumps(receipt, sort_keys=True)


def test_list_pending_review_uses_memory_gateway_conflict_route() -> None:
    receipt = public_adapter().list_pending_review(request("list_pending_review", "REVIEW"))

    assert receipt["payload"]["status"] == "PENDING_REVIEW_LISTED"
    assert receipt["payload"]["conflict_count"] == 0
    assert receipt["memory_class"] == "REVIEW"
