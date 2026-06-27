from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from core.memory_gateway import (
    ALLOWED_NAMESPACES,
    EXACT_CONFIRMATION_NOT_CANONICAL,
    MEMORY_GATEWAY_REQUEST_SCHEMA,
    EXACT_CONFIRMATION_REVISION_MISMATCH,
    GRAPH_RESULT_NOT_CANON_CONFIRMED,
    MemoryGateway,
    MemoryGatewayPolicyError,
    SEMANTIC_RESULT_NOT_CANON_CONFIRMED,
    STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE,
    allowed_command_names,
    capability_token,
)
from core.memory_override import MemoryOverrideRegistry
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    MemoryPatchProposalRegistry,
    canonical_dedupe_key,
    canonical_idempotency_key,
)


ROOT = Path(__file__).resolve().parents[1]


def gateway(*namespaces: str, public_mode: bool = True) -> MemoryGateway:
    return MemoryGateway(capability_token(namespaces=namespaces or ("aufmass",), public_mode=public_mode))


def request(namespace: str, suffix: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
        "namespace": namespace,
        "command": f"{namespace}.{suffix}",
        "payload": payload,
    }


def proposal(namespace: str = "aufmass", **overrides: object) -> dict[str, object]:
    source_hash = "0" * 64
    values: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": namespace,
        "project_id": namespace,
        "object_id": "object-001",
        "entity_scope": "room",
        "fact_type": "status",
        "normalized_target": "primary_fact",
        "source_evidence_hash": source_hash,
        "proposed_value": {"state": "ready"},
        "provenance_refs": [
            {"ref": f"exact-{namespace}-primary", "kind": "exact_source", "evidence_hash": source_hash}
        ],
        "actor_ref": "actor-001",
        "reason_code": "operator-confirmed",
        "approval_tier": "operator",
        "approval_ref": "approval-001",
        "confirmed_via_exact_ref": f"exact-{namespace}-primary",
        "confirmed_canonical_revision": 3,
    }
    values.update(overrides)
    values["dedupe_key"] = canonical_dedupe_key(values)
    values["idempotency_key"] = canonical_idempotency_key(values)
    return values


def test_valid_aufmass_exact_lookup_succeeds() -> None:
    result = gateway("aufmass").execute(request("aufmass", "memory.lookup_exact", {"key": "primary_fact"}))
    payload = result["payload"]

    assert result["namespace"] == "aufmass"
    assert payload["namespace"] == "aufmass"
    assert payload["authoritative"] is True
    assert payload["canonical_ref"] == "canon-aufmass-primary"
    assert payload["canonical_revision"] == 3
    assert payload["provenance_refs"][0]["kind"] == "exact_source"
    assert payload["source_kind"] == "canonical_sqlite"
    assert payload["authority_classification"] == "canonical_exact"


def test_aufmass_token_cannot_read_bauclock_namespace() -> None:
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway("aufmass").execute(request("bauclock", "memory.lookup_exact", {"key": "primary_fact"}))

    assert excinfo.value.reason_code == "NAMESPACE_NOT_AUTHORIZED"


def test_wildcard_namespace_access_fails_closed() -> None:
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        MemoryGateway(capability_token(namespaces=("*",)))

    assert excinfo.value.reason_code == "WILDCARD_NAMESPACE_FORBIDDEN"


def test_semantic_and_graph_results_are_structurally_non_authoritative() -> None:
    gw = gateway("aufmass")
    semantic = gw.search_semantic(namespace="aufmass", query="primary")
    graph = gw.query_code(namespace="aufmass", query="primary")

    semantic_result = semantic["payload"]["results"][0]
    graph_result = graph["payload"]["results"][0]
    assert semantic_result["authoritative"] is False
    assert semantic_result["authority_classification"] == "derived_semantic"
    assert semantic_result["source_kind"] == "mempalace"
    assert graph_result["authoritative"] is False
    assert graph_result["authoritative_scope"] == "code_graph"
    assert graph_result["authority_classification"] == "derived_code_graph"
    assert graph_result["source_kind"] == "graphify"


def test_semantic_backed_proposal_without_exact_confirmation_fails() -> None:
    candidate = proposal(
        provenance_refs=[
            {"ref": "semantic-ref-001", "kind": "semantic_only", "evidence_hash": "1" * 64}
        ],
        confirmed_via_exact_ref="",
    )

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway("aufmass").propose_patch(namespace="aufmass", proposal=candidate)

    assert excinfo.value.reason_code == SEMANTIC_RESULT_NOT_CANON_CONFIRMED


def test_graph_backed_proposal_without_exact_confirmation_fails() -> None:
    candidate = proposal(
        provenance_refs=[
            {"ref": "graph-ref-001", "kind": "code_graph", "evidence_hash": "2" * 64}
        ],
        confirmed_via_exact_ref="",
    )

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway("aufmass").propose_patch(namespace="aufmass", proposal=candidate)

    assert excinfo.value.reason_code == GRAPH_RESULT_NOT_CANON_CONFIRMED


def test_stale_index_backed_proposal_fails() -> None:
    source_hash = "0" * 64
    candidate = proposal(
        namespace="bauclock",
        source_evidence_hash=source_hash,
        provenance_refs=[
            {"ref": "semantic-ref-001", "kind": "semantic_only", "evidence_hash": "1" * 64},
            {"ref": "exact-bauclock-primary", "kind": "exact_source", "evidence_hash": source_hash},
        ],
        confirmed_via_exact_ref="exact-bauclock-primary",
    )

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway("bauclock").propose_patch(namespace="bauclock", proposal=candidate)

    assert excinfo.value.reason_code == STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE


def test_exact_confirmation_at_wrong_canonical_revision_fails() -> None:
    candidate = proposal(confirmed_canonical_revision=2)

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway("aufmass").propose_patch(namespace="aufmass", proposal=candidate)

    assert excinfo.value.reason_code == EXACT_CONFIRMATION_REVISION_MISMATCH


def test_fabricated_exact_ref_with_matching_revision_fails() -> None:
    source_hash = "a" * 64
    candidate = proposal(
        source_evidence_hash=source_hash,
        provenance_refs=[
            {"ref": "exact-fabricated-primary", "kind": "exact_source", "evidence_hash": source_hash}
        ],
        confirmed_via_exact_ref="exact-fabricated-primary",
        confirmed_canonical_revision=3,
    )

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway("aufmass").propose_patch(namespace="aufmass", proposal=candidate)

    assert excinfo.value.reason_code == EXACT_CONFIRMATION_NOT_CANONICAL


def test_current_canonical_exact_lookup_can_support_proposal() -> None:
    gw = gateway("aufmass")
    exact = gw.lookup_exact(namespace="aufmass", key="primary_fact")["payload"]
    exact_ref = exact["provenance_refs"][0]
    candidate = proposal(
        source_evidence_hash=exact_ref["evidence_hash"],
        provenance_refs=[exact_ref],
        confirmed_via_exact_ref=exact_ref["ref"],
        confirmed_canonical_revision=exact["canonical_revision"],
    )

    result = gw.propose_patch(namespace="aufmass", proposal=candidate)

    assert result["payload"]["proposal_event"]["status"] == "ACCEPTED"


def test_aufmass_conflict_query_never_returns_bauclock_conflict() -> None:
    patch_registry = MemoryPatchProposalRegistry()
    patch_registry.propose(proposal(namespace="bauclock"))
    patch_registry.propose(
        proposal(namespace="bauclock", proposed_value={"state": "blocked"}, approval_ref="approval-002")
    )
    gw = MemoryGateway(capability_token(namespaces=("aufmass", "bauclock")), patch_registry=patch_registry)

    result = gw.get_conflicts(namespace="aufmass")

    assert result["payload"]["conflicts"] == []


def test_conflict_and_override_queries_return_distinct_event_classes() -> None:
    patch_registry = MemoryPatchProposalRegistry()
    patch_registry.propose(proposal())
    patch_registry.propose(proposal(proposed_value={"state": "blocked"}, approval_ref="approval-002"))
    override_registry = MemoryOverrideRegistry()
    override = override_registry.propose_override(
        {
            "namespace": "aufmass",
            "project_id": "aufmass",
            "object_id": "object-001",
            "normalized_target": "primary_fact",
            "canonical_ref": "canon-aufmass-primary",
            "canonical_value": {"state": "ready"},
            "override_value": {"state": "blocked"},
            "actor_ref": "actor-001",
            "reason_code": "operator-override",
            "evidence_refs": [
                {"ref": "exact-ref-001", "kind": "exact_source", "evidence_hash": "0" * 64}
            ],
        }
    )
    gw = MemoryGateway(
        capability_token(namespaces=("aufmass",)),
        patch_registry=patch_registry,
        override_registry=override_registry,
    )

    conflicts = gw.get_conflicts(namespace="aufmass")["payload"]
    history = gw.get_override_history(namespace="aufmass", override_ref=str(override["override_ref"]))["payload"]

    assert conflicts["event_class"] == "source_value_conflict"
    assert conflicts["conflicts"][0]["conflict_ref"].startswith("proposal-conflict-")
    assert history["event_class"] == "override_lifecycle"
    assert history["events"][0]["event_type"] == "OVERRIDE_PROPOSAL"


def test_freshness_fields_are_mandatory_and_deterministic() -> None:
    gw = gateway("aufmass")
    memory = gw.get_memory_index_freshness(namespace="aufmass")["payload"]
    graph = gw.get_graph_index_freshness(namespace="aufmass")["payload"]["graphify"]

    assert set(graph) == {
        "indexed_repo_commit",
        "current_repo_commit",
        "indexed_at",
        "stale",
        "index_namespace",
    }
    assert set(memory["mempalace"]) == {
        "indexed_canonical_revision",
        "current_canonical_revision",
        "source_snapshot_id",
        "indexed_at",
        "stale",
        "index_namespace",
    }
    assert set(memory["canonical_sqlite"]) == {"current_canonical_revision"}
    assert json.dumps(memory, sort_keys=True) == json.dumps(memory, sort_keys=True)


def test_direct_storage_api_object_cannot_escape_gateway_schema() -> None:
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway("aufmass").lookup_exact(namespace="aufmass", key="/tmp/private.db")

    assert excinfo.value.reason_code == "UNSAFE_PUBLIC_PAYLOAD"


def test_no_private_looking_strings_or_paths_in_public_reports() -> None:
    gw = gateway("aufmass")
    gw.propose_patch(namespace="aufmass", proposal=proposal())

    reports = [
        gw.lookup_exact(namespace="aufmass", key="primary_fact"),
        gw.search_semantic(namespace="aufmass", query="primary"),
        gw.query_code(namespace="aufmass", query="primary"),
        gw.get_audit_log(namespace="aufmass"),
    ]
    serialized = json.dumps(reports, sort_keys=True).lower()

    assert "secret" not in serialized
    assert "password" not in serialized
    assert "credential" not in serialized
    assert "/tmp" not in serialized
    assert ".db" not in serialized
    assert ".sqlite" not in serialized


def test_required_namespaces_and_allowlisted_commands_are_registered() -> None:
    expected_namespaces = {"aufmass", "bauclock", "skeleton", "home_automation", "legal_private"}
    registry = yaml.safe_load((ROOT / "CAPABILITY_REGISTRY.yaml").read_text(encoding="utf-8"))
    gateway_capability = registry["capabilities"].get("memory_gateway")

    if gateway_capability is not None:
        assert set(gateway_capability["namespaces"]) == expected_namespaces
    else:
        assert set(ALLOWED_NAMESPACES) == expected_namespaces
    for namespace in expected_namespaces:
        assert set(allowed_command_names(namespace)) == {
            f"{namespace}.memory.lookup_exact",
            f"{namespace}.memory.search_semantic",
            f"{namespace}.memory.get_conflicts",
            f"{namespace}.memory.get_override_history",
            f"{namespace}.memory.get_audit_log",
            f"{namespace}.memory.get_index_freshness",
            f"{namespace}.graph.query_code",
            f"{namespace}.graph.get_index_freshness",
            f"{namespace}.memory.propose_patch",
        }
