from __future__ import annotations

from core.memory_override import MemoryOverrideRegistry, OVERRIDE_EVENT_TYPES
from core.memory_patch_proposal import stable_hash


def evidence_refs() -> list[dict[str, str]]:
    return [
        {
            "ref": "override-source-001",
            "kind": "exact_source",
            "evidence_hash": stable_hash({"source": "synthetic-as-built"}),
        }
    ]


def override_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "namespace": "synthetic-namespace",
        "project_id": "synthetic-project",
        "object_id": "synthetic-object",
        "normalized_target": "synthetic-target",
        "canonical_ref": "canonical-synthetic-000001",
        "canonical_value": {"state": "planned"},
        "override_value": {"state": "as-built"},
        "actor_ref": "synthetic-actor",
        "reason_code": "as-built-operator-override",
        "evidence_refs": evidence_refs(),
    }
    values.update(overrides)
    return values


def test_override_lifecycle_preserves_old_value_and_audit_chain() -> None:
    registry = MemoryOverrideRegistry()
    proposed = registry.propose_override(override_payload())
    approved = registry.approve_override(
        str(proposed["override_ref"]),
        actor_ref="operator-001",
        approval_ref="approval-override-001",
        evidence_refs=evidence_refs(),
    )
    activated = registry.activate_override(str(proposed["override_ref"]))
    active = registry.get_active_fact(
        namespace="synthetic-namespace",
        project_id="synthetic-project",
        object_id="synthetic-object",
        normalized_target="synthetic-target",
    )

    assert proposed["event_type"] == "OVERRIDE_PROPOSAL"
    assert approved["event_type"] == "OVERRIDE_APPROVAL"
    assert activated["event_type"] == "OVERRIDE_ACTIVATION"
    assert active is not None
    assert active["value"] == {"state": "as-built"}
    assert active["canonical_value"] == {"state": "planned"}
    assert active["canonical_ref"] == "canonical-synthetic-000001"
    assert [event["event_type"] for event in registry.get_override_history(str(proposed["override_ref"]))] == [
        "OVERRIDE_PROPOSAL",
        "OVERRIDE_APPROVAL",
        "OVERRIDE_ACTIVATION",
    ]


def test_override_history_and_conflict_queries_remain_distinct() -> None:
    registry = MemoryOverrideRegistry()
    proposed = registry.propose_override(override_payload())
    registry.approve_override(
        str(proposed["override_ref"]),
        actor_ref="operator-001",
        approval_ref="approval-override-001",
        evidence_refs=evidence_refs(),
    )
    registry.activate_override(str(proposed["override_ref"]))

    assert registry.get_conflicts() == []
    assert len(registry.get_override_history(str(proposed["override_ref"]))) == 3


def test_override_supersession_and_revocation_are_ordered_lifecycle_events() -> None:
    registry = MemoryOverrideRegistry()
    proposed = registry.propose_override(override_payload())
    registry.approve_override(
        str(proposed["override_ref"]),
        actor_ref="operator-001",
        approval_ref="approval-override-001",
        evidence_refs=evidence_refs(),
    )
    registry.activate_override(str(proposed["override_ref"]))
    registry.supersede_override(
        str(proposed["override_ref"]),
        replacement_override_ref="override-replacement-001",
        actor_ref="operator-001",
        approval_ref="approval-override-002",
    )

    second = registry.propose_override(override_payload(normalized_target="synthetic-target-b"))
    registry.approve_override(
        str(second["override_ref"]),
        actor_ref="operator-001",
        approval_ref="approval-override-003",
        evidence_refs=evidence_refs(),
    )
    registry.activate_override(str(second["override_ref"]))
    registry.revoke_override(
        str(second["override_ref"]),
        actor_ref="operator-001",
        approval_ref="approval-override-004",
    )

    history = registry.get_override_history(str(proposed["override_ref"]))
    second_history = registry.get_override_history(str(second["override_ref"]))

    assert [event["event_type"] for event in history] == [
        "OVERRIDE_PROPOSAL",
        "OVERRIDE_APPROVAL",
        "OVERRIDE_ACTIVATION",
        "OVERRIDE_SUPERSESSION",
    ]
    assert second_history[-1]["event_type"] == "OVERRIDE_REVOCATION"
    assert set(OVERRIDE_EVENT_TYPES) == {
        "OVERRIDE_PROPOSAL",
        "OVERRIDE_APPROVAL",
        "OVERRIDE_ACTIVATION",
        "OVERRIDE_SUPERSESSION",
        "OVERRIDE_REVOCATION",
    }
