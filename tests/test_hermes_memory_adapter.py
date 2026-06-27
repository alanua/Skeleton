from __future__ import annotations

import inspect

import pytest

from core.hermes_memory_adapter import (
    HERMES_MEMORY_OPERATIONS,
    HERMES_MEMORY_REQUEST_SCHEMA,
    HermesMemoryAdapter,
    HermesMemoryAdapterError,
)
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    MemoryPatchProposalRegistry,
    canonical_dedupe_key,
    canonical_idempotency_key,
)


def gateway() -> MemoryGateway:
    return MemoryGateway(capability_token(namespaces=("aufmass",)))


def request(project_id: str, operation: str, parameters: dict[str, object]) -> dict[str, object]:
    return {
        "schema": HERMES_MEMORY_REQUEST_SCHEMA,
        "namespace": "aufmass",
        "project_id": project_id,
        "operation": operation,
        "parameters": parameters,
    }


def proposal(project_id: str = "project-a", **overrides: object) -> dict[str, object]:
    source_hash = "0" * 64
    values: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": "aufmass",
        "project_id": project_id,
        "object_id": "object-001",
        "entity_scope": "room",
        "fact_type": "status",
        "normalized_target": "primary_fact",
        "source_evidence_hash": source_hash,
        "proposed_value": {"state": "ready"},
        "provenance_refs": [
            {
                "ref": f"exact-aufmass-{project_id}-primary",
                "kind": "exact_source",
                "evidence_hash": source_hash,
            }
        ],
        "actor_ref": "actor-001",
        "reason_code": "operator-confirmed",
        "approval_tier": "operator",
        "approval_ref": "approval-001",
        "confirmed_via_exact_ref": f"exact-aufmass-{project_id}-primary",
        "confirmed_canonical_revision": 3,
    }
    values.update(overrides)
    values["dedupe_key"] = canonical_dedupe_key(values)
    values["idempotency_key"] = canonical_idempotency_key(values)
    return values


def test_two_project_ids_under_aufmass_are_distinct_bindings() -> None:
    gw = gateway()
    project_a = HermesMemoryAdapter(gateway=gw, namespace="aufmass", project_id="project-a")
    project_b = HermesMemoryAdapter(gateway=gw, namespace="aufmass", project_id="project-b")

    result_a = project_a.run(request("project-a", "memory.lookup_exact", {"key": "primary_fact"}))
    result_b = project_b.run(request("project-b", "memory.lookup_exact", {"key": "primary_fact"}))

    assert result_a["namespace"] == "aufmass"
    assert result_a["project_id"] == "project-a"
    assert result_b["namespace"] == "aufmass"
    assert result_b["project_id"] == "project-b"


def test_hermes_memory_operations_are_exactly_six_memory_capabilities() -> None:
    assert HERMES_MEMORY_OPERATIONS == {
        "memory.lookup_exact",
        "memory.get_conflicts",
        "memory.get_override_history",
        "memory.get_audit_log",
        "memory.get_index_freshness",
        "memory.propose_patch",
    }


def test_adapter_rejects_gateway_semantic_and_graph_operations() -> None:
    adapter = HermesMemoryAdapter(gateway=gateway(), namespace="aufmass", project_id="project-a")

    for operation in ("memory.search_semantic", "graph.query_code", "graph.get_index_freshness"):
        with pytest.raises(HermesMemoryAdapterError) as excinfo:
            adapter.run(request("project-a", operation, {"query": "primary"}))
        assert excinfo.value.reason_code == "OPERATION_NOT_ALLOWLISTED"


def test_project_a_binding_cannot_read_or_propose_for_project_b() -> None:
    adapter = HermesMemoryAdapter(gateway=gateway(), namespace="aufmass", project_id="project-a")

    with pytest.raises(HermesMemoryAdapterError) as read_exc:
        adapter.run(request("project-b", "memory.lookup_exact", {"key": "primary_fact"}))
    assert read_exc.value.reason_code == "PROJECT_NOT_AUTHORIZED"

    with pytest.raises(HermesMemoryAdapterError) as propose_exc:
        adapter.run(
            request(
                "project-a",
                "memory.propose_patch",
                {"proposal": proposal(project_id="project-b")},
            )
        )
    assert propose_exc.value.reason_code == "PROJECT_NOT_AUTHORIZED"


def test_same_proposal_new_adapter_same_gateway_returns_duplicate_existing() -> None:
    patch_registry = MemoryPatchProposalRegistry()
    gw = MemoryGateway(capability_token(namespaces=("aufmass",)), patch_registry=patch_registry)
    first = HermesMemoryAdapter(gateway=gw, namespace="aufmass", project_id="project-a")
    second = HermesMemoryAdapter(gateway=gw, namespace="aufmass", project_id="project-a")
    packet = request("project-a", "memory.propose_patch", {"proposal": proposal()})

    first_result = first.run(packet)
    second_result = second.run(packet)

    assert first_result["status"] == "OPERATOR_APPROVAL_REQUIRED"
    assert first_result["decision"] == {
        "allowed": False,
        "reason": "canonical_write_requires_operator_approval",
    }
    assert second_result["status"] == "DUPLICATE_EXISTING"
    assert second_result["payload"]["classification"] == "DUPLICATE_EXISTING"


def test_adapter_uses_only_gateway_execute_for_memory_access() -> None:
    class FakeGateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def execute(self, packet: dict[str, object]) -> dict[str, object]:
            self.calls.append(packet)
            return {
                "schema": "skeleton.memory_gateway.response.v1",
                "contract_version": "1.0.0",
                "namespace": "aufmass",
                "command": "aufmass.memory.lookup_exact",
                "payload": {
                    "namespace": "aufmass",
                    "project_id": "project-a",
                    "authoritative": True,
                    "authority_classification": "canonical_exact",
                    "source_kind": "canonical_sqlite",
                    "canonical_ref": "canon-aufmass-primary",
                    "canonical_revision": 3,
                },
            }

    fake = FakeGateway()
    adapter = HermesMemoryAdapter(gateway=fake, namespace="aufmass", project_id="project-a")  # type: ignore[arg-type]

    result = adapter.run(request("project-a", "memory.lookup_exact", {"key": "primary_fact"}))

    assert result["status"] == "DRY_RUN_OK"
    assert len(fake.calls) == 1
    source = inspect.getsource(HermesMemoryAdapter)
    assert "sqlite" not in source.lower()
    assert "graphify" not in source.lower()
    assert "mempalace" not in source.lower()
    assert "open(" not in source
