from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from core.memory_gateway_storage import PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA, PrivateMemoryGatewayStorage
from core.memory_gateway import (
    MEMORY_GATEWAY_REQUEST_SCHEMA,
    MemoryGateway,
    allowed_command_names,
    capability_token,
)
from core.private_memory_stack import PrivateMemoryStack
from core.skeleton_memory import SkeletonMemory
from core.canonical_memory_manifest import (
    APPROVED_OPERATOR_RULE_CATEGORIES,
    canonical_manifest_integrity_hash,
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


def proposal(namespace: str = "aufmass", project_id: str | None = None, **overrides: object) -> dict[str, object]:
    source_hash = "0" * 64
    project_id = project_id or namespace
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
            {"ref": "exact-bauclock-bauclock-primary", "kind": "exact_source", "evidence_hash": source_hash},
        ],
        confirmed_via_exact_ref="exact-bauclock-bauclock-primary",
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


def test_aufmass_conflict_query_excludes_bauclock_conflicts() -> None:
    patch_registry = MemoryPatchProposalRegistry()
    patch_registry.propose(proposal(namespace="bauclock"))
    patch_registry.propose(
        proposal(namespace="bauclock", proposed_value={"state": "blocked"}, approval_ref="approval-002")
    )
    gw = MemoryGateway(capability_token(namespaces=("aufmass", "bauclock")), patch_registry=patch_registry)

    result = gw.get_conflicts(namespace="aufmass")

    assert result["payload"]["conflicts"] == []


def test_aufmass_project_a_cannot_read_project_b_scoped_state() -> None:
    patch_registry = MemoryPatchProposalRegistry()
    project_b_proposal = proposal(
        namespace="aufmass",
        project_id="project-b",
        proposed_value={"state": "blocked"},
        approval_ref="approval-b",
    )
    patch_registry.propose(project_b_proposal)
    patch_registry.propose(
        proposal(
            namespace="aufmass",
            project_id="project-b",
            proposed_value={"state": "changed"},
            approval_ref="approval-b2",
        )
    )
    override_registry = MemoryOverrideRegistry()
    override = override_registry.propose_override(
        {
            "namespace": "aufmass",
            "project_id": "project-b",
            "object_id": "object-001",
            "normalized_target": "primary_fact",
            "canonical_ref": "canon-aufmass-project-b-primary",
            "canonical_value": {"state": "ready-project-b"},
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
    gw.propose_patch(namespace="aufmass", project_id="project-b", proposal=project_b_proposal)

    project_a_exact = gw.lookup_exact(namespace="aufmass", project_id="project-a", key="primary_fact")
    project_b_exact = gw.lookup_exact(namespace="aufmass", project_id="project-b", key="primary_fact")

    assert project_a_exact["payload"]["canonical_ref"] == "canon-aufmass-project-a-primary"
    assert project_b_exact["payload"]["canonical_ref"] == "canon-aufmass-project-b-primary"
    assert gw.get_conflicts(namespace="aufmass", project_id="project-a")["payload"]["conflicts"] == []
    assert (
        gw.get_override_history(
            namespace="aufmass",
            project_id="project-a",
            override_ref=str(override["override_ref"]),
        )["payload"]["events"]
        == []
    )
    assert gw.get_audit_log(namespace="aufmass", project_id="project-a")["payload"]["events"] == []
    assert (
        gw.get_memory_index_freshness(namespace="aufmass", project_id="project-a")["payload"]["mempalace"][
            "source_snapshot_id"
        ]
        != gw.get_memory_index_freshness(namespace="aufmass", project_id="project-b")["payload"]["mempalace"][
            "source_snapshot_id"
        ]
    )


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
        "project_id",
    }
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
        gateway("aufmass").lookup_exact(
            namespace="aufmass",
            key="/tmp/private.db",
        )

    assert excinfo.value.reason_code == "INVALID_LOOKUP_KEY"


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


def test_canonical_manifest_preparation_readback_is_non_authoritative() -> None:
    manifest = json.loads(
        (ROOT / "fixtures" / "canonical_memory" / "operator_preferences_fast_autonomous_execution_v1.json").read_text(
            encoding="utf-8"
        )
    )
    gw = gateway("skeleton")

    result = gw.execute(request("skeleton", "memory.prepare_canonical_manifest", {"manifest": manifest}))
    payload = result["payload"]

    assert payload["preparation_status"] == "PREPARED_FOR_OPERATOR_REVIEW"
    assert payload["authority"] == "candidate_manifest_only"
    assert payload["authoritative"] is False
    assert payload["source_kind"] == "canonical_memory_manifest"
    assert payload["integrity_check"] == "verified"
    assert payload["integrity_hash"] == canonical_manifest_integrity_hash(manifest)
    assert "manifest" not in payload
    assert "value" not in payload
    assert "normalized_manifest_json" not in payload

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.lookup_exact(namespace="skeleton", key="fast_autonomous_execution_v1")

    assert excinfo.value.reason_code == "CANONICAL_FACT_NOT_FOUND"


def test_canonical_manifest_import_command_reads_back_authoritative() -> None:
    manifest = json.loads(
        (ROOT / "fixtures" / "canonical_memory" / "operator_preferences_fast_autonomous_execution_v1.json").read_text(
            encoding="utf-8"
        )
    )
    memory = SkeletonMemory()
    gw = MemoryGateway(capability_token(namespaces=("skeleton",)), skeleton_memory=memory)

    result = gw.execute(request("skeleton", "memory.import_canonical_manifest", {"manifest": manifest}))
    payload = result["payload"]
    exact = gw.lookup_exact(namespace="skeleton", project_id="skeleton", key="fast_autonomous_execution_v1")[
        "payload"
    ]

    assert payload["idempotency_classification"] == "NEW_IMPORT"
    assert payload["snapshot_status"] == "created"
    assert payload["read_back_status"] == "verified"
    assert payload["rollback_status"] == "not_required"
    assert payload["authoritative"] is True
    assert exact["authoritative"] is True
    assert exact["canonical_revision"] == payload["canonical_revision"]
    assert exact["integrity_hash"] == canonical_manifest_integrity_hash(manifest)



def test_gateway_public_receipts_do_not_expose_raw_values_or_manifests() -> None:
    manifest = json.loads(
        (ROOT / "fixtures" / "canonical_memory" / "operator_preferences_fast_autonomous_execution_v1.json").read_text(
            encoding="utf-8"
        )
    )
    memory = SkeletonMemory()
    gw = MemoryGateway(capability_token(namespaces=("skeleton",)), skeleton_memory=memory)

    prepared = gw.execute(request("skeleton", "memory.prepare_canonical_manifest", {"manifest": manifest}))["payload"]
    imported = gw.execute(request("skeleton", "memory.import_canonical_manifest", {"manifest": manifest}))["payload"]
    exact = gw.lookup_exact(namespace="skeleton", project_id="skeleton", key="fast_autonomous_execution_v1")["payload"]

    serialized = json.dumps([prepared, imported, exact], sort_keys=True)
    assert "manifest" not in prepared
    assert "normalized_manifest_json" not in exact
    assert "value" not in exact
    assert "operating_rules" not in serialized
    assert imported["authoritative"] is True
    assert exact["authoritative"] is True

def test_canonical_manifest_preparation_rejects_invalid_manifest() -> None:
    manifest = json.loads(
        (ROOT / "fixtures" / "canonical_memory" / "operator_preferences_fast_autonomous_execution_v1.json").read_text(
            encoding="utf-8"
        )
    )
    manifest["authority"] = "canonical_sqlite"

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway("skeleton").execute(
            request("skeleton", "memory.prepare_canonical_manifest", {"manifest": manifest})
        )

    assert excinfo.value.reason_code == "INVALID_CANONICAL_MANIFEST"


def test_canonical_manifest_preparation_is_skeleton_namespace_only() -> None:
    manifest = json.loads(
        (ROOT / "fixtures" / "canonical_memory" / "operator_preferences_fast_autonomous_execution_v1.json").read_text(
            encoding="utf-8"
        )
    )

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway("aufmass").execute(request("aufmass", "memory.prepare_canonical_manifest", {"manifest": manifest}))

    assert excinfo.value.reason_code == "CANONICAL_MANIFEST_NAMESPACE_NOT_AUTHORIZED"


def test_required_namespaces_and_allowlisted_commands_are_registered() -> None:
    expected_namespaces = {"aufmass", "bauclock", "skeleton", "home_automation", "legal_private"}
    registry = yaml.safe_load((ROOT / "CAPABILITY_REGISTRY.yaml").read_text(encoding="utf-8"))
    gateway_capability = registry["capabilities"]["memory_gateway"]

    assert set(gateway_capability["namespaces"]) == expected_namespaces
    for namespace in expected_namespaces:
        assert set(allowed_command_names(namespace)) == {
            f"{namespace}.memory.lookup_exact",
            f"{namespace}.memory.search_semantic",
            f"{namespace}.memory.get_conflicts",
            f"{namespace}.memory.get_override_history",
            f"{namespace}.memory.get_audit_log",
            f"{namespace}.memory.get_index_freshness",
            f"{namespace}.memory.prepare_canonical_manifest",
            f"{namespace}.memory.import_canonical_manifest",
            f"{namespace}.memory.private_mutate",
            f"{namespace}.graph.query_code",
            f"{namespace}.graph.get_index_freshness",
            f"{namespace}.memory.propose_patch",
        }


def test_private_memory_gateway_put_is_idempotent_and_exact_readback_is_authoritative(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    gw = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    before = stack.status()["canonical_sqlite"]["canonical_revision"]
    mutation = {
        "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
        "project_id": "skeleton",
        "operation": "put",
        "fact_namespace": "skeleton.notes",
        "fact_id": "gateway_note",
        "value": {"summary": "gateway exact readback"},
        "actor_ref": "operator",
        "reason_code": "operator-put",
        "approval_ref": "local-operator",
        "expected_revision": before,
        "idempotency_key": "idem_gateway_note",
    }

    first = gw.execute(request("skeleton", "memory.private_mutate", mutation))["payload"]
    revision_after_first = stack.status()["canonical_sqlite"]["canonical_revision"]
    second = gw.execute(request("skeleton", "memory.private_mutate", mutation))["payload"]
    exact = stack.get(namespace="skeleton.notes", fact_id="gateway_note")

    assert first["operation"] == "put"
    assert first["idempotency_classification"] == "NEW_MUTATION"
    assert first["canonical_revision"] == before + 1
    assert second["idempotency_classification"] == "DUPLICATE_IDENTICAL"
    assert stack.status()["canonical_sqlite"]["canonical_revision"] == revision_after_first
    assert exact["authoritative"] is True
    assert exact["canonical_revision"] == first["canonical_revision"]
    assert exact["value"]["summary"] == "gateway exact readback"
    serialized = json.dumps([first, second], sort_keys=True)
    assert "gateway exact readback" not in serialized
    assert str(tmp_path) not in serialized
    assert ".sqlite" not in serialized


def test_private_memory_gateway_rejects_mismatched_idempotency_reuse(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    gw = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    mutation = {
        "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
        "project_id": "skeleton",
        "operation": "put",
        "fact_namespace": "skeleton.notes",
        "fact_id": "idem_note",
        "value": {"summary": "first"},
        "idempotency_key": "idem_reuse",
    }
    gw.execute(request("skeleton", "memory.private_mutate", mutation))

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.execute(
            request(
                "skeleton",
                "memory.private_mutate",
                {**mutation, "value": {"summary": "different"}},
            )
        )

    assert excinfo.value.reason_code == "MemoryGatewayStorageError"


def test_private_memory_gateway_crash_retry_does_not_advance_revision_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    adapter = PrivateMemoryGatewayStorage(stack)
    gw = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=adapter,
    )
    mutation = {
        "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
        "project_id": "skeleton",
        "operation": "put",
        "fact_namespace": "skeleton.notes",
        "fact_id": "crash_note",
        "value": {"summary": "committed before receipt"},
        "idempotency_key": "idem_crash_note",
    }
    before = stack.status()["canonical_sqlite"]["canonical_revision"]
    original_record_done = adapter._record_done

    def crash_after_commit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic receipt write crash")

    monkeypatch.setattr(adapter, "_record_done", crash_after_commit)
    with pytest.raises(MemoryGatewayPolicyError):
        gw.execute(request("skeleton", "memory.private_mutate", mutation))
    after_crash = stack.status()["canonical_sqlite"]["canonical_revision"]
    monkeypatch.setattr(adapter, "_record_done", original_record_done)

    recovered = gw.execute(request("skeleton", "memory.private_mutate", mutation))["payload"]

    assert after_crash == before + 1
    assert stack.status()["canonical_sqlite"]["canonical_revision"] == after_crash
    assert recovered["idempotency_classification"] == "DUPLICATE_IDENTICAL"
    assert recovered["canonical_revision"] == after_crash
    assert stack.get(namespace="skeleton.notes", fact_id="crash_note")["value"]["summary"] == "committed before receipt"


def test_private_memory_gateway_rejects_public_mode_and_wrong_project(tmp_path: Path) -> None:
    stack = PrivateMemoryStack(tmp_path)
    public_gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=True),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    mutation = {
        "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
        "project_id": "skeleton",
        "operation": "delete",
        "fact_namespace": "skeleton.notes",
        "fact_id": "blocked",
    }

    with pytest.raises(MemoryGatewayPolicyError) as public_exc:
        public_gateway.execute(request("skeleton", "memory.private_mutate", mutation))
    assert public_exc.value.reason_code == "PRIVATE_MEMORY_MUTATION_PUBLIC_MODE_FORBIDDEN"

    private_gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    with pytest.raises(MemoryGatewayPolicyError) as project_exc:
        private_gateway.execute(
            request("skeleton", "memory.private_mutate", {**mutation, "project_id": "other"})
        )
    assert project_exc.value.reason_code == "MemoryGatewayStorageError"
