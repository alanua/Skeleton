from __future__ import annotations

import json

import pytest

from core.hermes_memory_adapter import (
    HERMES_MEMORY_REQUEST_SCHEMA,
    HermesMemoryAdapter,
    HermesMemoryAdapterError,
)
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    canonical_dedupe_key,
    canonical_idempotency_key,
)


def request(capability: str, payload: dict[str, object], **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema": HERMES_MEMORY_REQUEST_SCHEMA,
        "namespace": "aufmass",
        "project_id": "aufmass",
        "capability": capability,
        "payload": payload,
    }
    values.update(overrides)
    return values


def proposal(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": "aufmass",
        "project_id": "aufmass",
        "object_id": "synthetic-room-rule",
        "entity_scope": "room",
        "fact_type": "room_rule",
        "normalized_target": "synthetic_room_rule",
        "source_evidence_hash": "3" * 64,
        "proposed_value": {
            "state": "candidate-review",
            "rule_code": "synthetic-room-rule-v1",
        },
        "provenance_refs": [
            {
                "ref": "exact-aufmass-synthetic-room-rule",
                "kind": "exact_source",
                "evidence_hash": "3" * 64,
            }
        ],
        "actor_ref": "hermes-synthetic",
        "reason_code": "synthetic-candidate-source",
        "approval_tier": "operator",
        "approval_ref": "approval-synthetic-001",
        "confirmed_via_exact_ref": "exact-aufmass-synthetic-room-rule",
        "confirmed_canonical_revision": 3,
    }
    values.update(overrides)
    values["dedupe_key"] = canonical_dedupe_key(values)
    values["idempotency_key"] = canonical_idempotency_key(values)
    return values


def test_namespace_binding_enforced() -> None:
    adapter = HermesMemoryAdapter()

    with pytest.raises(HermesMemoryAdapterError) as excinfo:
        adapter.execute(request("memory.lookup_exact", {"key": "synthetic_room_rule"}, namespace="bauclock"))

    assert excinfo.value.reason_code == "PROJECT_NAMESPACE_MISMATCH"


def test_cross_project_request_blocked() -> None:
    adapter = HermesMemoryAdapter()

    with pytest.raises(HermesMemoryAdapterError) as excinfo:
        adapter.execute(request("memory.lookup_exact", {"key": "synthetic_room_rule"}, project_id="bauclock"))

    assert excinfo.value.reason_code == "PROJECT_NAMESPACE_MISMATCH"


def test_direct_storage_call_blocked() -> None:
    adapter = HermesMemoryAdapter()

    with pytest.raises(HermesMemoryAdapterError) as excinfo:
        adapter.execute(
            request(
                "memory.lookup_exact",
                {"key": "synthetic_room_rule", "storage_api": "sqlite"},
            )
        )

    assert excinfo.value.reason_code == "DIRECT_STORAGE_ACCESS_BLOCKED"


def test_canonical_exact_lookup_includes_revision_and_provenance() -> None:
    result = HermesMemoryAdapter().execute(request("memory.lookup_exact", {"key": "synthetic_room_rule"}))
    payload = result["payload"]

    assert result["authoritative"] is True
    assert payload["authority_classification"] == "canonical_exact"
    assert payload["canonical_revision"] == 3
    assert payload["provenance_refs"][0]["ref"] == "exact-aufmass-synthetic-room-rule"


def test_proposal_missing_deterministic_keys_blocked() -> None:
    candidate = proposal()
    candidate["dedupe_key"] = "memory-dedupe:v1:" + "9" * 64

    with pytest.raises(HermesMemoryAdapterError) as excinfo:
        HermesMemoryAdapter().execute(request("memory.propose_patch", {"proposal": candidate}))

    assert excinfo.value.reason_code == "DETERMINISTIC_KEYS_REQUIRED"


def test_override_requires_distinct_intent_and_approval() -> None:
    adapter = HermesMemoryAdapter()
    missing_intent = proposal(approval_tier="override_operator", approval_ref="approval-override-001")
    weak_tier = proposal(
        override_intent=True,
        approval_tier="operator",
        approval_ref="approval-override-002",
    )

    with pytest.raises(HermesMemoryAdapterError) as first:
        adapter.execute(request("memory.propose_patch", {"proposal": missing_intent}))
    with pytest.raises(HermesMemoryAdapterError) as second:
        adapter.execute(request("memory.propose_patch", {"proposal": weak_tier}))

    assert first.value.reason_code == "OVERRIDE_INTENT_REQUIRED"
    assert second.value.reason_code == "OVERRIDE_APPROVAL_TIER_REQUIRED"


def test_repeated_proposal_is_idempotent() -> None:
    adapter = HermesMemoryAdapter()
    candidate = proposal()

    first = adapter.execute(request("memory.propose_patch", {"proposal": candidate}))
    repeated = adapter.execute(request("memory.propose_patch", {"proposal": candidate}))

    assert first["write_gate"]["outcome"] == "APPROVED_FOR_OPERATOR"
    assert repeated["write_gate"]["outcome"] == "DUPLICATE_EXISTING"
    assert repeated["payload"]["proposal_event"]["event_ref"] == first["payload"]["proposal_event"]["event_ref"]
    assert repeated["canonical_write_performed"] is False


def test_public_result_is_privacy_safe() -> None:
    adapter = HermesMemoryAdapter()
    reports = [
        adapter.execute(request("memory.lookup_exact", {"key": "synthetic_room_rule"})),
        adapter.execute(request("memory.get_index_freshness", {})),
        adapter.execute(request("memory.get_audit_log", {})),
    ]
    serialized = json.dumps(reports, sort_keys=True).lower()

    assert "secret" not in serialized
    assert "password" not in serialized
    assert "credential" not in serialized
    assert "/tmp" not in serialized
    assert ".db" not in serialized
    assert ".sqlite" not in serialized
