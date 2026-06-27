from __future__ import annotations

import json

import pytest

from core.memory_gateway import (
    MEMORY_GATEWAY_REQUEST_SCHEMA,
    MemoryGateway,
    allowed_command_names,
    capability_token,
    stable_project_hash,
)
from core.memory_gateway_policy import (
    EXACT_CONFIRMATION_REVISION_MISMATCH,
    GRAPH_RESULT_NOT_CANON_CONFIRMED,
    SEMANTIC_RESULT_NOT_CANON_CONFIRMED,
    STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE,
    MemoryGatewayPolicyError,
)
from core.memory_override import MemoryOverrideRegistry
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    MemoryPatchProposalRegistry,
    canonical_dedupe_key,
    canonical_idempotency_key,
)

def gateway(*namespaces: str, project_id: str | None = None, public_mode: bool = True) -> MemoryGateway:
    return MemoryGateway(
        capability_token(
            namespaces=namespaces or ("aufmass",),
            project_id=project_id,
            public_mode=public_mode,
        )
    )


def request(namespace: str, suffix: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
        "namespace": namespace,
        "command": f"{namespace}.{suffix}",
        "payload": payload,
    }


def proposal(namespace: str = "aufmass", project_id: str | None = None, **overrides: object) -> dict[str, object]:
    project_id = str(overrides.get("project_id", project_id or namespace))
    source_hash = stable_project_hash(namespace, project_id)
    values: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": namespace,
        "project_id": project_id,
        "object_id": "object-001",
        "entity_scope": "room",
        "fact_type": "status",
        "normalized_target": "primary_fact",
        "source_evidence_hash": source_hash,
        "proposed_value": {"state": "ready"},
        "provenance_refs": [
            {
                "ref": f"exact-{namespace}-{project_id}-primary",
                "kind": "exact_source",
                "evidence_hash": source_hash,
            }
        ],
        "actor_ref": "actor-001",
        "reason_code": "operator-confirmed",
        "approval_tier": "operator",
        "approval_ref": "approval-001",
        "confirmed_via_exact_ref": f"exact-{namespace}-{project_id}-primary",
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
    assert payload["project_id"] == "aufmass"
    assert payload["authoritative"] is True
    assert payload["canonical_ref"] == "canon-aufmass-aufmass-primary"
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


def test_semantic_and_graph_commands_are_not_gateway_capabilities() -> None:
    gw = gateway("aufmass")

    for suffix in ("memory.search_semantic", "graph.query_code", "graph.get_index_freshness"):
        with pytest.raises(MemoryGatewayPolicyError) as excinfo:
            gw.execute(request("aufmass", suffix, {"query": "primary"}))
        assert excinfo.value.reason_code == "COMMAND_NOT_ALLOWLISTED"


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
    source_hash = stable_project_hash("bauclock", "bauclock")
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


def test_fabricated_exact_confirmation_is_rejected() -> None:
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

    assert excinfo.value.reason_code == "EXACT_CONFIRMATION_NOT_CANONICAL"


def test_genuine_current_exact_confirmation_succeeds() -> None:
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


def test_same_proposal_through_new_gateway_with_shared_registry_is_duplicate_existing() -> None:
    patch_registry = MemoryPatchProposalRegistry()
    first_gateway = MemoryGateway(
        capability_token(namespaces=("aufmass",), project_id="project-a"),
        patch_registry=patch_registry,
    )
    second_gateway = MemoryGateway(
        capability_token(namespaces=("aufmass",), project_id="project-a"),
        patch_registry=patch_registry,
    )
    candidate = proposal(project_id="project-a")

    first = first_gateway.propose_patch(namespace="aufmass", proposal=candidate)
    duplicate = second_gateway.propose_patch(namespace="aufmass", proposal=candidate)

    assert first["payload"]["proposal_event"]["status"] == "ACCEPTED"
    assert duplicate["payload"]["proposal_event"]["status"] == "DUPLICATE_EXISTING"
    assert (
        duplicate["payload"]["proposal_event"]["event_ref"]
        == first["payload"]["proposal_event"]["event_ref"]
    )


def test_aufmass_conflict_query_excludes_bauclock_conflicts() -> None:
    patch_registry = MemoryPatchProposalRegistry()
    patch_registry.propose(proposal(namespace="bauclock"))
    patch_registry.propose(
        proposal(namespace="bauclock", proposed_value={"state": "blocked"}, approval_ref="approval-002")
    )
    gw = MemoryGateway(capability_token(namespaces=("aufmass", "bauclock")), patch_registry=patch_registry)

    result = gw.get_conflicts(namespace="aufmass")

    assert result["payload"]["conflicts"] == []


def test_aufmass_project_bindings_have_distinct_canonical_results() -> None:
    project_a = gateway("aufmass", project_id="project-a")
    project_b = gateway("aufmass", project_id="project-b")

    result_a = project_a.lookup_exact(namespace="aufmass", key="primary_fact")["payload"]
    result_b = project_b.lookup_exact(namespace="aufmass", key="primary_fact")["payload"]

    assert result_a["project_id"] == "project-a"
    assert result_b["project_id"] == "project-b"
    assert result_a["canonical_ref"] == "canon-aufmass-project-a-primary"
    assert result_b["canonical_ref"] == "canon-aufmass-project-b-primary"
    assert result_a["value"] != result_b["value"]


def test_project_b_cannot_receive_project_a_reports() -> None:
    patch_registry = MemoryPatchProposalRegistry()
    project_a_proposal = proposal(project_id="project-a")
    patch_registry.propose(project_a_proposal)
    patch_registry.propose(
        proposal(project_id="project-a", proposed_value={"state": "blocked"}, approval_ref="approval-002")
    )
    override_registry = MemoryOverrideRegistry()
    override = override_registry.propose_override(
        {
            "namespace": "aufmass",
            "project_id": "project-a",
            "object_id": "object-001",
            "normalized_target": "primary_fact",
            "canonical_ref": "canon-aufmass-project-a-primary",
            "canonical_value": {"state": "ready-project-a"},
            "override_value": {"state": "blocked"},
            "actor_ref": "actor-001",
            "reason_code": "operator-override",
            "evidence_refs": [
                {
                    "ref": "exact-aufmass-project-a-primary",
                    "kind": "exact_source",
                    "evidence_hash": stable_project_hash("aufmass", "project-a"),
                }
            ],
        }
    )
    project_a = MemoryGateway(
        capability_token(namespaces=("aufmass",), project_id="project-a"),
        patch_registry=patch_registry,
        override_registry=override_registry,
    )
    project_a.propose_patch(namespace="aufmass", proposal=project_a_proposal)
    project_b = MemoryGateway(
        capability_token(namespaces=("aufmass",), project_id="project-b"),
        patch_registry=patch_registry,
        override_registry=override_registry,
    )

    assert project_b.get_conflicts(namespace="aufmass")["payload"]["conflicts"] == []
    assert (
        project_b.get_override_history(
            namespace="aufmass",
            override_ref=str(override["override_ref"]),
        )["payload"]["events"]
        == []
    )
    assert project_b.get_audit_log(namespace="aufmass")["payload"]["events"] == []


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
    assert set(memory["mempalace"]) == {
        "indexed_canonical_revision",
        "current_canonical_revision",
        "source_snapshot_id",
        "indexed_at",
        "stale",
        "index_namespace",
        "project_id",
    }
    assert set(memory["canonical_sqlite"]) == {"current_canonical_revision", "project_id"}
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
    required_suffixes = {
        "memory.lookup_exact",
        "memory.get_conflicts",
        "memory.get_override_history",
        "memory.get_audit_log",
        "memory.get_index_freshness",
        "memory.propose_patch",
    }
    for namespace in expected_namespaces:
        assert set(allowed_command_names(namespace)) == {
            f"{namespace}.{suffix}" for suffix in required_suffixes
        }
