from __future__ import annotations

import json

import pytest

from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    MemoryPatchProposalIdempotencyError,
    MemoryPatchProposalRegistry,
    MemoryPatchProposalValidationError,
    canonical_dedupe_key,
    canonical_idempotency_key,
    stable_hash,
)


def proposal(**overrides: object) -> dict[str, object]:
    source_hash = stable_hash({"source": "synthetic-source", "line": 1})
    values: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": "synthetic-namespace",
        "project_id": "synthetic-project",
        "object_id": "synthetic-object",
        "entity_scope": "room",
        "fact_type": "status",
        "normalized_target": "synthetic-target",
        "source_evidence_hash": source_hash,
        "proposed_value": {"state": "ready"},
        "provenance_refs": [
            {"ref": "source-ref-001", "kind": "exact_source", "evidence_hash": source_hash}
        ],
        "actor_ref": "synthetic-actor",
        "reason_code": "operator-confirmed",
        "approval_tier": "operator",
        "approval_ref": "approval-001",
        "confirmed_via_exact_ref": "source-ref-001",
        "confirmed_canonical_revision": 7,
    }
    values.update(overrides)
    values["dedupe_key"] = canonical_dedupe_key(values)
    values["idempotency_key"] = canonical_idempotency_key(values)
    return values


def test_exact_duplicate_returns_existing_ref() -> None:
    registry = MemoryPatchProposalRegistry()
    first = registry.propose(proposal())
    duplicate = registry.propose(proposal())

    assert duplicate["status"] == "ACCEPTED"
    assert duplicate["event_ref"] == first["event_ref"]
    assert duplicate["canonical_ref"] == first["canonical_ref"]


def test_same_idempotency_key_with_changed_payload_fails() -> None:
    registry = MemoryPatchProposalRegistry()
    first_payload = proposal()
    registry.propose(first_payload)
    changed = proposal(proposed_value={"state": "changed"})
    changed["idempotency_key"] = first_payload["idempotency_key"]

    with pytest.raises(MemoryPatchProposalIdempotencyError):
        registry.propose(changed)


def test_two_distinct_facts_from_one_source_are_not_merged() -> None:
    registry = MemoryPatchProposalRegistry()
    first = registry.propose(proposal(normalized_target="synthetic-target-a"))
    second = registry.propose(proposal(normalized_target="synthetic-target-b"))

    assert first["status"] == "ACCEPTED"
    assert second["status"] == "ACCEPTED"
    assert first["canonical_ref"] != second["canonical_ref"]
    assert not registry.get_conflicts()


def test_conflicting_values_become_review_required() -> None:
    registry = MemoryPatchProposalRegistry()
    registry.propose(proposal())
    changed_hash = stable_hash({"source": "synthetic-source", "line": 2})
    candidate = proposal(
        source_evidence_hash=changed_hash,
        proposed_value={"state": "blocked"},
        provenance_refs=[
            {"ref": "source-ref-002", "kind": "exact_source", "evidence_hash": changed_hash}
        ],
        confirmed_via_exact_ref="source-ref-002",
        approval_ref="approval-002",
    )

    result = registry.propose(candidate)

    assert result["status"] == "REVIEW_REQUIRED"
    assert result["canonical_ref"] is None
    assert result["conflict_ref"] == registry.get_conflicts()[0]["conflict_ref"]


def test_semantic_only_proposal_is_rejected() -> None:
    source_hash = stable_hash({"source": "semantic-summary"})
    candidate = proposal(
        source_evidence_hash=source_hash,
        provenance_refs=[
            {"ref": "semantic-ref-001", "kind": "semantic_only", "evidence_hash": source_hash}
        ],
        confirmed_via_exact_ref="semantic-ref-001",
    )

    with pytest.raises(MemoryPatchProposalValidationError):
        MemoryPatchProposalRegistry().propose(candidate)


def test_namespace_mismatch_fails_closed() -> None:
    candidate = proposal()
    candidate["namespace"] = "other-namespace"

    with pytest.raises(MemoryPatchProposalValidationError):
        MemoryPatchProposalRegistry().propose(candidate)


def test_missing_or_malformed_keys_fail_closed() -> None:
    missing = proposal()
    missing.pop("dedupe_key")
    malformed = proposal()
    malformed["dedupe_key"] = "not-canonical"

    with pytest.raises(MemoryPatchProposalValidationError):
        MemoryPatchProposalRegistry().propose(missing)
    with pytest.raises(MemoryPatchProposalValidationError):
        MemoryPatchProposalRegistry().propose(malformed)


def test_no_private_looking_value_leaks_to_public_reports() -> None:
    candidate = proposal(proposed_value={"state": "secret-token"})

    with pytest.raises(MemoryPatchProposalValidationError):
        MemoryPatchProposalRegistry().propose(candidate)

    result = MemoryPatchProposalRegistry().propose(proposal())
    serialized = json.dumps(result, sort_keys=True).lower()
    assert "secret" not in serialized
    assert "token" not in serialized
    assert "/" not in serialized
