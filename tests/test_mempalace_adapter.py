from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from core.mempalace_adapter import MemPalaceAdapter
from core.mempalace_projection import MEMPALACE_SYNTHETIC_NAMESPACE, MEMPALACE_SYNTHETIC_PROJECT_ID, load_projection
from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_policy import STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE, MemoryGatewayPolicyError
from core.memory_patch_proposal import PATCH_PROPOSAL_SCHEMA, canonical_dedupe_key, canonical_idempotency_key
from scripts.mempalace_synthetic_benchmark import SYNTHETIC_PROJECTION, run_benchmark


ROOT = Path(__file__).resolve().parents[1]
PROJECTION_PATH = ROOT / "tests" / "fixtures" / "mempalace_synthetic" / "projection.json"


def projection() -> dict[str, object]:
    if PROJECTION_PATH.exists():
        return json.loads(PROJECTION_PATH.read_text(encoding="utf-8"))
    return deepcopy(SYNTHETIC_PROJECTION)


def gateway(adapter: MemPalaceAdapter | None = None) -> MemoryGateway:
    return MemoryGateway(
        capability_token(namespaces=(MEMPALACE_SYNTHETIC_NAMESPACE,)),
        mempalace_adapter=adapter or MemPalaceAdapter(projection()),
    )


def gateway_request(query: str) -> dict[str, object]:
    return {
        "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
        "namespace": MEMPALACE_SYNTHETIC_NAMESPACE,
        "command": f"{MEMPALACE_SYNTHETIC_NAMESPACE}.memory.search_semantic",
        "payload": {"project_id": MEMPALACE_SYNTHETIC_PROJECT_ID, "query": query},
    }


def proposal_from_result(result: dict[str, object], *, confirmed_revision: int) -> dict[str, object]:
    attribution = result["source_attribution"][0]
    source_hash = attribution["evidence_hash"]
    values: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": MEMPALACE_SYNTHETIC_NAMESPACE,
        "project_id": MEMPALACE_SYNTHETIC_PROJECT_ID,
        "object_id": "synthetic-object-001",
        "entity_scope": "synthetic",
        "fact_type": "status",
        "normalized_target": result["result_refs"][1],
        "source_evidence_hash": source_hash,
        "proposed_value": {"state": "ready"},
        "provenance_refs": [
            {"ref": attribution["source_ref"], "kind": "exact_source", "evidence_hash": source_hash},
            {"ref": result["result_refs"][0], "kind": "semantic_only", "evidence_hash": source_hash},
        ],
        "actor_ref": "actor-001",
        "reason_code": "operator-confirmed",
        "approval_tier": "operator",
        "approval_ref": "approval-001",
        "confirmed_via_exact_ref": attribution["source_ref"],
        "confirmed_canonical_revision": confirmed_revision,
    }
    values["dedupe_key"] = canonical_dedupe_key(values)
    values["idempotency_key"] = canonical_idempotency_key(values)
    return values


def test_normal_gateway_construction_does_not_load_mempalace_fixture() -> None:
    gw = MemoryGateway(capability_token(namespaces=(MEMPALACE_SYNTHETIC_NAMESPACE,)))

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.execute(gateway_request("door attribution"))

    assert excinfo.value.reason_code == "MEMPALACE_ADAPTER_NOT_CONFIGURED"


def test_projection_loader_validates_synthetic_public_safe_projection() -> None:
    loaded = load_projection(projection())

    assert loaded.namespace == MEMPALACE_SYNTHETIC_NAMESPACE
    assert loaded.project_id == MEMPALACE_SYNTHETIC_PROJECT_ID


def test_namespace_isolation_and_synthetic_project_binding() -> None:
    gw = gateway()

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": "bauclock",
                "command": "bauclock.memory.search_semantic",
                "payload": {"project_id": MEMPALACE_SYNTHETIC_PROJECT_ID, "query": "door"},
            }
        )

    assert excinfo.value.reason_code == "NAMESPACE_NOT_AUTHORIZED"


def test_gateway_marks_all_synthetic_results_non_authoritative() -> None:
    payload = gateway().execute(gateway_request("door attribution"))["payload"]

    assert payload["authoritative"] is False
    assert payload["results"]
    assert all(result["authoritative"] is False for result in payload["results"])


def test_source_attribution_mandatory_on_every_result() -> None:
    results = gateway().execute(gateway_request("quality caution"))["payload"]["results"]

    assert results
    assert all(result["source_attribution"] for result in results)
    assert all(set(result["source_attribution"][0]) == {"source_ref", "kind", "evidence_hash"} for result in results)


def test_required_result_fields_are_returned() -> None:
    result = gateway().execute(gateway_request("ventilation timing"))["payload"]["results"][0]

    assert set(result) == {
        "schema",
        "authoritative",
        "namespace",
        "project_id",
        "result_refs",
        "source_attribution",
        "score",
        "indexed_canonical_revision",
        "current_canonical_revision",
        "source_snapshot_id",
        "indexed_at",
        "stale",
        "bounded_text",
    }
    assert result["namespace"] == MEMPALACE_SYNTHETIC_NAMESPACE
    assert len(result["bounded_text"]) <= 180


def test_stale_semantic_evidence_rejected_without_proposal_event() -> None:
    stale_projection = projection()
    stale_projection["current_canonical_revision"] = 4
    adapter = MemPalaceAdapter(stale_projection)
    gw = gateway(adapter)
    result = gw.execute(gateway_request("door attribution"))["payload"]["results"][0]
    candidate = proposal_from_result(result, confirmed_revision=4)

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.propose_patch(
            namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
            project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
            proposal=candidate,
        )

    assert excinfo.value.reason_code == STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE
    audit = gw.get_audit_log(namespace=MEMPALACE_SYNTHETIC_NAMESPACE, project_id=MEMPALACE_SYNTHETIC_PROJECT_ID)
    assert audit["payload"]["events"] == []


def test_deletion_removes_retrieval_result() -> None:
    adapter = MemPalaceAdapter(projection()).delete_item("synthetic-door-policy")
    results = adapter.search_semantic(
        namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
        project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
        query="door attribution",
    )["results"]

    assert all(result["result_refs"][0] != "synthetic-door-policy" for result in results)


def test_rebuild_reproduces_deterministic_index_manifest() -> None:
    adapter = MemPalaceAdapter(projection())

    assert adapter.manifest == adapter.rebuild_manifest()


def test_unsupported_arbitrary_field_rejected() -> None:
    candidate = projection()
    candidate["documents"][0]["raw_reviewer_comment"] = "unbounded arbitrary field"

    with pytest.raises(Exception) as excinfo:
        load_projection(candidate)

    assert getattr(excinfo.value, "reason_code") == "UNSUPPORTED_PROJECTION_FIELD"


def test_no_private_looking_values_or_paths_in_public_report() -> None:
    report = run_benchmark()
    serialized = json.dumps(report, sort_keys=True).lower()

    for forbidden in ("legal", "contact", "address", "phone", "email", "secret", "password", "/tmp", ".db"):
        assert forbidden not in serialized


def test_benchmark_emits_pass_and_exits_zero_only_for_pass() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/mempalace_synthetic_benchmark.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert report["decision"] == "PASS"
    assert report["stable_reasons"] == [
        "namespace_isolation_proven",
        "deletion_and_rebuild_pass",
        "source_attribution_present",
        "synthetic_quality_threshold_met",
        "bounded_resources_documented",
    ]
    assert set(report["resource_report"]) == {
        "aggregate_disk_bytes",
        "aggregate_ram_bytes",
        "aggregate_build_ms",
    }
