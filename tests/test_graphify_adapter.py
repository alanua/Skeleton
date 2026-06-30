from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from core.graphify_adapter import (
    GRAPHIFY_MAX_RESULTS,
    GRAPHIFY_SYNTHETIC_NAMESPACE,
    GRAPHIFY_SYNTHETIC_PROJECT_ID,
    GraphifyAdapter,
    GraphifyAdapterError,
    load_synthetic_fixture,
)
from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_policy import STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE, MemoryGatewayPolicyError
from core.memory_patch_proposal import PATCH_PROPOSAL_SCHEMA, canonical_dedupe_key, canonical_idempotency_key


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "graphify_synthetic" / "graph.json"
GRAPH_QUERY_SCHEMA_PATH = ROOT / "schemas" / "graph_memory_query.schema.json"


def fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def gateway(data: dict[str, object] | None = None) -> MemoryGateway:
    return MemoryGateway(
        capability_token(namespaces=(GRAPHIFY_SYNTHETIC_NAMESPACE,)),
        graphify_adapter=GraphifyAdapter(data or fixture()),
    )


def graph_request(query_kind: str, **payload: object) -> dict[str, object]:
    values: dict[str, object] = {
        "project_id": GRAPHIFY_SYNTHETIC_PROJECT_ID,
        "query": query_kind,
    }
    values.update(payload)
    return {
        "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
        "namespace": GRAPHIFY_SYNTHETIC_NAMESPACE,
        "command": f"{GRAPHIFY_SYNTHETIC_NAMESPACE}.graph.query_code",
        "payload": values,
    }


def proposal_from_result(result: dict[str, object], **overrides: object) -> dict[str, object]:
    source_attribution = result["source_attribution"][0]
    provenance = result["provenance_refs"][0]
    values: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": GRAPHIFY_SYNTHETIC_NAMESPACE,
        "project_id": GRAPHIFY_SYNTHETIC_PROJECT_ID,
        "object_id": "synthetic-object-graph",
        "entity_scope": "code",
        "fact_type": "relationship",
        "normalized_target": str(result["result_ref"]),
        "source_evidence_hash": source_attribution["evidence_hash"],
        "proposed_value": {"state": "reviewed"},
        "provenance_refs": [provenance],
        "actor_ref": "actor-001",
        "reason_code": "operator-confirmed",
        "approval_tier": "operator",
        "approval_ref": "approval-001",
        "confirmed_via_exact_ref": source_attribution["source_ref"],
        "confirmed_canonical_revision": 3,
    }
    values.update(overrides)
    values["dedupe_key"] = canonical_dedupe_key(values)
    values["idempotency_key"] = canonical_idempotency_key(values)
    return values


def test_normal_gateway_requires_explicit_adapter_for_synthetic_graph_route() -> None:
    gw = MemoryGateway(capability_token(namespaces=(GRAPHIFY_SYNTHETIC_NAMESPACE,)))

    assert getattr(gw, "_graphify_adapter") is None
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.execute(graph_request("module_relationship"))

    assert excinfo.value.reason_code == "GRAPHIFY_ADAPTER_REQUIRED"


def test_approved_query_returns_deterministic_bounded_attributed_results() -> None:
    payload = gateway().execute(graph_request("module_relationship"))["payload"]
    result = payload["results"][0]

    assert len(payload["results"]) == 1
    assert result["result_ref"] == "graph-result-module-gateway-adapter"
    assert result["authoritative"] is False
    assert result["authority_classification"] == "derived_code_graph"
    assert result["source_attribution"] == [
        {
            "source_ref": "src-synthetic-module-gateway-adapter",
            "kind": "synthetic_code_relationship",
            "evidence_hash": "a" * 64,
        }
    ]
    assert result["indexed_repo_commit"] == "commit-indexed-graph-0001"
    assert result["current_repo_commit"] == "commit-indexed-graph-0001"
    assert result["indexed_at"] == "2026-06-29T00:00:00Z"
    assert result["graph_schema_version"] == "skeleton.graphify.synthetic_graph.v1"
    assert result["stale"] is False
    assert payload["query_report"]["aggregate_counts"]["relationship_count"] == 1
    assert json.dumps(payload, sort_keys=True) == json.dumps(payload, sort_keys=True)


def test_public_query_report_validates_against_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    report = gateway().execute(graph_request("dependency_relationship"))["payload"]["query_report"]
    schema = json.loads(GRAPH_QUERY_SCHEMA_PATH.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator(schema).validate(report)


def test_unsupported_kind_and_wrong_scope_fail_closed() -> None:
    gw = gateway()

    with pytest.raises(MemoryGatewayPolicyError) as unsupported:
        gw.execute(graph_request("raw_traversal"))
    assert unsupported.value.reason_code == "GRAPHIFY_QUERY_KIND_NOT_ALLOWLISTED"

    with pytest.raises(MemoryGatewayPolicyError) as wrong_project:
        gw.query_code(namespace=GRAPHIFY_SYNTHETIC_NAMESPACE, project_id="wrong-project", query="module_relationship")
    assert wrong_project.value.reason_code == "PROJECT_NOT_AUTHORIZED"

    with pytest.raises(MemoryGatewayPolicyError) as wrong_namespace:
        MemoryGateway(
            capability_token(namespaces=("aufmass",)),
            graphify_adapter=GraphifyAdapter(fixture()),
        ).query_code(namespace="aufmass", project_id=GRAPHIFY_SYNTHETIC_PROJECT_ID, query="module_relationship")
    assert wrong_namespace.value.reason_code == "PROJECT_NOT_AUTHORIZED"


def test_private_path_value_malformed_fixture_missing_provenance_and_excessive_results_fail_closed() -> None:
    private_value = fixture()
    private_value["relationships"][0]["source_ref"] = "src-synthetic-private-value"
    with pytest.raises(GraphifyAdapterError) as private_exc:
        GraphifyAdapter(private_value)
    assert private_exc.value.reason_code == "GRAPHIFY_PRIVATE_VALUE_REJECTED"

    malformed = fixture()
    malformed["relationships"][0]["raw_payload"] = {"node_id": "node-001"}
    with pytest.raises(GraphifyAdapterError) as malformed_exc:
        load_synthetic_fixture(malformed)
    assert malformed_exc.value.reason_code == "GRAPHIFY_FIXTURE_MALFORMED"

    missing_provenance = fixture()
    del missing_provenance["relationships"][0]["source_ref"]
    with pytest.raises(GraphifyAdapterError) as provenance_exc:
        GraphifyAdapter(missing_provenance)
    assert provenance_exc.value.reason_code == "GRAPHIFY_MISSING_PROVENANCE"

    excessive = fixture()
    excessive["relationships"] = [
        {
            "result_ref": f"graph-result-module-extra-{index}",
            "query_kind": "module_relationship",
            "relationship_kind": "Extra synthetic module relationship",
            "source_ref": f"src-synthetic-module-extra-{index}",
            "evidence_hash": f"{index + 1:064x}"[-64:],
            "related_refs": [f"module-extra-{index}", "module-core-graphify_adapter"],
            "deleted": False,
        }
        for index in range(GRAPHIFY_MAX_RESULTS + 1)
    ]
    with pytest.raises(MemoryGatewayPolicyError) as excessive_exc:
        gateway(excessive).execute(graph_request("module_relationship"))
    assert excessive_exc.value.reason_code == "GRAPHIFY_RESULT_LIMIT_EXCEEDED"


def test_stale_graph_readable_but_cannot_support_patch_proposal() -> None:
    stale_fixture = fixture()
    stale_fixture["current_repo_commit"] = "commit-current-graph-0002"
    gw = gateway(stale_fixture)
    result = gw.execute(graph_request("module_relationship"))["payload"]["results"][0]
    candidate = proposal_from_result(result)

    assert result["stale"] is True
    assert result["authoritative"] is False
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.propose_patch(
            namespace=GRAPHIFY_SYNTHETIC_NAMESPACE,
            project_id=GRAPHIFY_SYNTHETIC_PROJECT_ID,
            proposal=candidate,
        )

    assert excinfo.value.reason_code == STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE


def test_freshness_reports_exact_indexed_current_commit_and_stale_state() -> None:
    stale_fixture = fixture()
    stale_fixture["current_repo_commit"] = "commit-current-graph-0002"

    graphify = gateway(stale_fixture).get_graph_index_freshness(
        namespace=GRAPHIFY_SYNTHETIC_NAMESPACE,
        project_id=GRAPHIFY_SYNTHETIC_PROJECT_ID,
    )["payload"]["graphify"]

    assert graphify == {
        "indexed_repo_commit": "commit-indexed-graph-0001",
        "current_repo_commit": "commit-current-graph-0002",
        "indexed_at": "2026-06-29T00:00:00Z",
        "stale": True,
        "index_namespace": GRAPHIFY_SYNTHETIC_NAMESPACE,
        "project_id": GRAPHIFY_SYNTHETIC_PROJECT_ID,
        "graph_schema_version": "skeleton.graphify.synthetic_graph.v1",
        "authoritative": False,
        "authority_classification": "derived_code_graph",
    }


def test_deleted_fixture_items_and_raw_private_markers_do_not_escape_public_output() -> None:
    payload = gateway().execute(graph_request("module_relationship"))["payload"]
    serialized = json.dumps(payload, sort_keys=True).lower()

    assert "graph-result-deleted-legacy" not in serialized
    for forbidden in (
        "node_id",
        "edge_id",
        "raw_payload",
        "private-value",
        "secret",
        "password",
        "credential",
        "/tmp",
        ".db",
        "local_path",
    ):
        assert forbidden not in serialized


def test_adapter_does_not_call_subprocess_network_service_write_or_runtime_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("runtime execution is forbidden")

    monkeypatch.setattr(subprocess, "run", forbidden_call)

    gw = gateway()
    result = gw.execute(graph_request("provenance_relationship"))

    assert result["payload"]["query_report"]["synthetic_only"] is True
