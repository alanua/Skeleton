from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.hermes_memory_adapter import (
    HERMES_MEMORY_CAPABILITIES,
    HERMES_MEMORY_REQUEST_SCHEMA,
    HermesMemoryAdapter,
)
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_policy import MemoryGatewayPolicyError
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    MemoryPatchProposalRegistry,
    canonical_dedupe_key,
    canonical_idempotency_key,
)


ROOT = Path(__file__).resolve().parents[1]


def adapter(
    *,
    namespace: str = "aufmass",
    project_id: str = "project-a",
    registry: MemoryPatchProposalRegistry | None = None,
) -> HermesMemoryAdapter:
    gateway = MemoryGateway(
        capability_token(namespaces=(namespace,)),
        patch_registry=registry,
    )
    return HermesMemoryAdapter(gateway=gateway, namespace=namespace, project_id=project_id)


def packet(capability: str, payload: dict[str, object] | None = None, project_id: str = "project-a") -> dict[str, object]:
    return {
        "schema": HERMES_MEMORY_REQUEST_SCHEMA,
        "namespace": "aufmass",
        "project_id": project_id,
        "capability": capability,
        "payload": payload or {},
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


def test_hermes_capability_scope_is_exactly_six_memory_operations() -> None:
    assert HERMES_MEMORY_CAPABILITIES == (
        "lookup_exact",
        "get_conflicts",
        "get_override_history",
        "get_audit_log",
        "get_index_freshness",
        "propose_patch",
    )


def test_adapter_exact_lookup_is_bound_to_one_namespace_project_pair() -> None:
    result = adapter().run(packet("lookup_exact", {"key": "primary_fact"}))

    assert result["namespace"] == "aufmass"
    assert result["project_id"] == "project-a"
    assert result["payload"]["canonical_ref"] == "canon-aufmass-project-a-primary"


def test_adapter_rejects_cross_project_packet() -> None:
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        adapter().run(packet("lookup_exact", {"key": "primary_fact"}, project_id="project-b"))

    assert excinfo.value.reason_code == "HERMES_MEMORY_SCOPE_MISMATCH"


def test_adapter_rejects_malformed_and_gateway_forbidden_capability_packets() -> None:
    with pytest.raises(MemoryGatewayPolicyError):
        adapter().run({"schema": "wrong", "namespace": "aufmass", "project_id": "project-a"})
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        adapter().run(packet("search_semantic", {"query": "primary"}))

    assert excinfo.value.reason_code == "HERMES_MEMORY_CAPABILITY_FORBIDDEN"


def test_exact_duplicate_across_new_adapter_with_shared_registry_is_duplicate_existing() -> None:
    registry = MemoryPatchProposalRegistry()
    first = adapter(registry=registry).run(packet("propose_patch", {"proposal": proposal()}))
    second = adapter(registry=registry).run(packet("propose_patch", {"proposal": proposal()}))

    assert first["payload"]["proposal_event"]["status"] == "ACCEPTED"
    assert second["payload"]["proposal_event"]["status"] == "DUPLICATE_EXISTING"
    assert second["payload"]["proposal_event"]["event_ref"] == first["payload"]["proposal_event"]["event_ref"]


def test_same_target_changed_payload_stays_review_required() -> None:
    registry = MemoryPatchProposalRegistry()
    first = adapter(registry=registry).run(packet("propose_patch", {"proposal": proposal()}))
    changed = proposal(proposed_value={"state": "changed"}, approval_ref="approval-002")
    second = adapter(registry=registry).run(packet("propose_patch", {"proposal": changed}))

    assert first["payload"]["proposal_event"]["status"] == "ACCEPTED"
    assert second["payload"]["proposal_event"]["status"] == "REVIEW_REQUIRED"


def test_adapter_and_registry_do_not_access_private_registry_fields() -> None:
    sources = [
        (ROOT / "core" / "hermes_memory_adapter.py").read_text(encoding="utf-8"),
        (ROOT / "tests" / "test_hermes_memory_adapter.py").read_text(encoding="utf-8"),
    ]
    joined = "\n".join(sources)

    forbidden_attrs = [
        "_event_by_idempotency",
        "_payload_hash_by_idempotency",
        "_events_by_dedupe",
    ]
    for attr in forbidden_attrs:
        assert f".{attr}" not in joined
    assert json.dumps(adapter().run(packet("get_index_freshness")), sort_keys=True)
