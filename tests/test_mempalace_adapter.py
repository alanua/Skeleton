from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from core.mempalace_adapter import LocalMemPalaceIndex, MemPalaceAdapter, MemPalaceAdapterError
from core.mempalace_projection import MEMPALACE_SYNTHETIC_NAMESPACE, MEMPALACE_SYNTHETIC_PROJECT_ID, load_projection
from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_policy import STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE, MemoryGatewayPolicyError
from core.memory_patch_proposal import (
    PATCH_PROPOSAL_SCHEMA,
    MemoryPatchProposalRegistry,
    canonical_dedupe_key,
    canonical_idempotency_key,
)
from scripts import mempalace_synthetic_benchmark
from scripts.mempalace_synthetic_benchmark import run_benchmark


ROOT = Path(__file__).resolve().parents[1]
PROJECTION_PATH = ROOT / "tests" / "fixtures" / "mempalace_synthetic" / "projection.json"
PROJECTION_SCHEMA_PATH = ROOT / "schemas" / "mempalace_projection.schema.json"
RESULT_SCHEMA_PATH = ROOT / "schemas" / "mempalace_result.schema.json"


def projection() -> dict[str, object]:
    return json.loads(PROJECTION_PATH.read_text(encoding="utf-8"))


def gateway() -> MemoryGateway:
    return MemoryGateway(
        capability_token(namespaces=(MEMPALACE_SYNTHETIC_NAMESPACE,)),
        mempalace_adapter=MemPalaceAdapter(projection()),
    )


def gateway_request(query: str) -> dict[str, object]:
    return {
        "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
        "namespace": MEMPALACE_SYNTHETIC_NAMESPACE,
        "command": f"{MEMPALACE_SYNTHETIC_NAMESPACE}.memory.search_semantic",
        "payload": {"project_id": MEMPALACE_SYNTHETIC_PROJECT_ID, "query": query},
    }


def proposal_from_result(result: dict[str, object], **overrides: object) -> dict[str, object]:
    source_attribution = result["source_attribution"][0]
    values: dict[str, object] = {
        "schema": PATCH_PROPOSAL_SCHEMA,
        "namespace": MEMPALACE_SYNTHETIC_NAMESPACE,
        "project_id": MEMPALACE_SYNTHETIC_PROJECT_ID,
        "object_id": "synthetic-object-001",
        "entity_scope": "pilot",
        "fact_type": "status",
        "normalized_target": str(result["result_refs"][0]),
        "source_evidence_hash": source_attribution["evidence_hash"],
        "proposed_value": {"state": "reviewed"},
        "provenance_refs": [
            {
                "ref": source_attribution["source_ref"],
                "kind": "semantic_only",
                "evidence_hash": source_attribution["evidence_hash"],
                "indexed_canonical_revision": result["indexed_canonical_revision"],
                "current_canonical_revision": result["current_canonical_revision"],
                "stale": result["stale"],
            }
        ],
        "actor_ref": "actor-001",
        "reason_code": "operator-confirmed",
        "approval_tier": "operator",
        "approval_ref": "approval-001",
        "confirmed_via_exact_ref": source_attribution["source_ref"],
        "confirmed_canonical_revision": result["current_canonical_revision"],
    }
    values.update(overrides)
    values["dedupe_key"] = canonical_dedupe_key(values)
    values["idempotency_key"] = canonical_idempotency_key(values)
    return values


def test_normal_gateway_does_not_load_synthetic_fixture_or_adapter() -> None:
    gw = MemoryGateway(capability_token(namespaces=(MEMPALACE_SYNTHETIC_NAMESPACE,)))

    assert getattr(gw, "_mempalace_adapter") is None
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.execute(gateway_request("door attribution"))

    assert excinfo.value.reason_code == "MEMPALACE_ADAPTER_REQUIRED"


def test_projection_and_result_schemas_validate_fixture_and_result() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator(json.loads(PROJECTION_SCHEMA_PATH.read_text(encoding="utf-8"))).validate(
        projection()
    )

    result_schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    result = gateway().execute(gateway_request("door attribution"))["payload"]["results"][0]

    jsonschema.Draft202012Validator(result_schema).validate(result)


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


def test_stale_mempalace_proposal_rejected_without_audit_event() -> None:
    stale_projection = projection()
    stale_projection["current_canonical_revision"] = 4
    patch_registry = MemoryPatchProposalRegistry()
    gw = MemoryGateway(
        capability_token(namespaces=(MEMPALACE_SYNTHETIC_NAMESPACE,)),
        patch_registry=patch_registry,
        mempalace_adapter=MemPalaceAdapter(stale_projection),
    )
    result = gw.execute(gateway_request("door attribution"))["payload"]["results"][0]
    candidate = proposal_from_result(result)

    assert result["stale"] is True
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.propose_patch(
            namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
            project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
            proposal=candidate,
        )

    assert excinfo.value.reason_code == STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE
    audit = gw.get_audit_log(
        namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
        project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
    )
    assert audit["payload"]["events"] == []
    assert patch_registry.lookup_by_idempotency_key(candidate["idempotency_key"]) is None


def test_mempalace_result_cannot_self_confirm_canonical_exact_evidence() -> None:
    gw = gateway()
    result = gw.execute(gateway_request("door attribution"))["payload"]["results"][0]
    candidate = proposal_from_result(result)

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.propose_patch(
            namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
            project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
            proposal=candidate,
        )

    assert excinfo.value.reason_code == "EXACT_CONFIRMATION_NOT_CANONICAL"
    audit = gw.get_audit_log(
        namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
        project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
    )
    assert audit["payload"]["events"] == []


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


def test_stale_revision_surfaces_and_blocks_proposal_use() -> None:
    adapter = MemPalaceAdapter(projection())
    freshness = adapter.get_index_freshness(
        namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
        project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
        current_canonical_revision=4,
    )
    result = adapter.search_semantic(
        namespace=MEMPALACE_SYNTHETIC_NAMESPACE,
        project_id=MEMPALACE_SYNTHETIC_PROJECT_ID,
        query="door attribution",
        current_canonical_revision=4,
    )["results"][0]

    assert freshness["stale"] is True
    assert result["stale"] is True
    assert result["authoritative"] is False


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


def test_local_mempalace_index_validates_hash_and_counts_on_status_and_load(tmp_path: Path) -> None:
    index_path = tmp_path / "mempalace.index.json"
    facts = [
        {
            "namespace": "skeleton.notes",
            "fact_id": "note1",
            "canonical_ref": "skeleton.notes:note1",
            "value": {"summary": "alpha beta"},
            "value_hash": "a" * 64,
            "canonical_revision": 1,
        }
    ]
    LocalMemPalaceIndex.rebuild_from_facts(index_path, facts=facts, canonical_revision=1)
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["documents"][0]["tokens"].append("tampered")
    index_path.write_text(json.dumps(payload), encoding="utf-8")

    assert LocalMemPalaceIndex.status(index_path, current_canonical_revision=1)["state"] == "BLOCKED"
    with pytest.raises(MemPalaceAdapterError):
        LocalMemPalaceIndex(index_path)


def test_local_mempalace_rejects_empty_queries(tmp_path: Path) -> None:
    index_path = tmp_path / "mempalace.index.json"
    LocalMemPalaceIndex.rebuild_from_facts(index_path, facts=[], canonical_revision=1)

    with pytest.raises(MemPalaceAdapterError):
        LocalMemPalaceIndex(index_path).search(query="   ")


def test_unsupported_arbitrary_field_rejected() -> None:
    candidate = deepcopy(projection())
    candidate["documents"][0]["raw_reviewer_comment"] = "unbounded arbitrary field"

    with pytest.raises(Exception) as excinfo:
        load_projection(candidate)

    assert getattr(excinfo.value, "reason_code") == "UNSUPPORTED_PROJECTION_FIELD"


def test_no_private_looking_values_or_paths_in_public_report() -> None:
    report = run_benchmark()
    serialized = json.dumps(report, sort_keys=True).lower()

    for forbidden in ("legal", "contact", "address", "phone", "email", "secret", "password", "/tmp", ".db"):
        assert forbidden not in serialized


def test_benchmark_emits_stable_pass_caution_reject_decision() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/mempalace_synthetic_benchmark.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert report["decision"] in {"PASS", "CAUTION", "REJECT"}
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


def test_benchmark_main_exits_zero_only_for_pass(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        mempalace_synthetic_benchmark,
        "run_benchmark",
        lambda: {"decision": "PASS", "stable_reasons": [], "checks": []},
    )
    assert mempalace_synthetic_benchmark.main() == 0

    monkeypatch.setattr(
        mempalace_synthetic_benchmark,
        "run_benchmark",
        lambda: {"decision": "CAUTION", "stable_reasons": [], "checks": []},
    )
    assert mempalace_synthetic_benchmark.main() == 1

    monkeypatch.setattr(
        mempalace_synthetic_benchmark,
        "run_benchmark",
        lambda: {"decision": "REJECT", "stable_reasons": [], "checks": []},
    )
    assert mempalace_synthetic_benchmark.main() == 1
    capsys.readouterr()
