from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.capability_runtime_truth import (
    RuntimeCapabilityEvidence,
    reconcile_capability_runtime_truth,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "capability_runtime_truth.schema.json"
OLD_PUBLIC_FIELDS = {
    "effective_state",
    "freshness",
    "drift",
    "parity",
    "registry_effective_state",
    "fresh_verified_runtime_states",
    "evidence_binding",
    "source_presence",
}


def registry() -> dict:
    return {
        "version": "1.0.0",
        "capabilities": {
            "available_capability": {
                "status": "available",
                "module": "core/example.py",
                "tested": True,
            },
            "planned_capability": {
                "status": "planned",
                "module": "core/future.py",
                "tested": False,
            },
        },
    }


def test_reconciler_preserves_registry_authority_without_evidence() -> None:
    truth = reconcile_capability_runtime_truth(
        registry(),
        now="2026-10-02T00:00:00Z",
    )

    assert truth["runtime_probe_performed"] is False
    assert truth["runtime_mutation_performed"] is False
    assert truth["checked_at"] == "2026-10-02T00:00:00Z"
    assert {
        key: truth["summary"][key]
        for key in (
            "registry_capability_count",
            "registry_available_count",
            "typed_runtime_evidence_count",
            "accepted_runtime_evidence_count",
            "derived_available_count",
            "derived_degraded_count",
            "derived_unavailable_count",
            "registry_runtime_conflict_count",
            "runtime_override_count",
        )
    } == {
        "registry_capability_count": 2,
        "registry_available_count": 1,
        "typed_runtime_evidence_count": 0,
        "accepted_runtime_evidence_count": 0,
        "derived_available_count": 0,
        "derived_degraded_count": 0,
        "derived_unavailable_count": 2,
        "registry_runtime_conflict_count": 0,
        "runtime_override_count": 0,
    }
    assert {item["derived_runtime_state"] for item in truth["capabilities"]} == {
        "unavailable"
    }
    assert {item["live_runtime_execution"] for item in truth["capabilities"]} == {False}
    assert {item["runtime_evidence_state"] for item in truth["capabilities"]} == {
        "no_runtime_evidence"
    }
    assert {item["registry_runtime_relation"] for item in truth["capabilities"]} == {
        "registry_only"
    }
    assert {item["source_state"] for item in truth["capabilities"]} == {
        "not_checked"
    }
    assert all(
        "NO_RUNTIME_EVIDENCE" in item["reason_codes"]
        for item in truth["capabilities"]
    )
    assert all(
        not (OLD_PUBLIC_FIELDS & item.keys()) for item in truth["capabilities"]
    )


def test_accepted_runtime_evidence_derives_state_without_mutating_registry() -> None:
    source_registry = registry()
    truth = reconcile_capability_runtime_truth(
        source_registry,
        [
            RuntimeCapabilityEvidence(
                capability_id="available_capability",
                runtime_state="available",
                source="fixture",
                evidence_ref="typed",
            ),
            RuntimeCapabilityEvidence(
                capability_id="planned_capability",
                runtime_state="available",
                source="fixture",
                evidence_ref="typed",
            ),
        ],
    )

    capabilities = {item["capability_id"]: item for item in truth["capabilities"]}

    assert capabilities["available_capability"]["derived_runtime_state"] == "available"
    assert capabilities["available_capability"]["live_runtime_execution"] is True
    assert capabilities["available_capability"]["registry_runtime_relation"] == (
        "runtime_matches_registry"
    )
    assert capabilities["planned_capability"]["registry_status"] == "planned"
    assert capabilities["planned_capability"]["registry_declared_state"] == (
        "declared_unavailable"
    )
    assert capabilities["planned_capability"]["derived_runtime_state"] == "available"
    assert capabilities["planned_capability"]["registry_runtime_relation"] == (
        "runtime_overrides_registry"
    )
    assert "ACCEPTED_RUNTIME_EVIDENCE" in capabilities["planned_capability"]["reason_codes"]
    assert source_registry["capabilities"]["planned_capability"]["status"] == "planned"


def test_stale_or_unverified_runtime_evidence_cannot_be_accepted_for_truth() -> None:
    truth = reconcile_capability_runtime_truth(
        registry(),
        [
            RuntimeCapabilityEvidence(
                capability_id="planned_capability",
                runtime_state="available",
                source="fixture",
                evidence_ref="stale",
                fresh=False,
            ),
            RuntimeCapabilityEvidence(
                capability_id="available_capability",
                runtime_state="unavailable",
                source="fixture",
                evidence_ref="unverified",
                verified=False,
            ),
        ],
    )

    capabilities = {item["capability_id"]: item for item in truth["capabilities"]}

    assert capabilities["planned_capability"]["derived_runtime_state"] == "unavailable"
    assert capabilities["planned_capability"]["runtime_evidence_state"] == (
        "stale_runtime_evidence"
    )
    assert capabilities["planned_capability"]["registry_runtime_relation"] == (
        "runtime_not_accepted"
    )
    assert capabilities["available_capability"]["derived_runtime_state"] == "unavailable"
    assert capabilities["available_capability"]["live_runtime_execution"] is False
    assert all(
        evidence["accepted_for_truth"] is False
        for item in capabilities.values()
        for evidence in item["runtime_evidence"]
    )


def test_conflicting_accepted_runtime_evidence_uses_most_restrictive_state() -> None:
    truth = reconcile_capability_runtime_truth(
        registry(),
        [
            RuntimeCapabilityEvidence(
                capability_id="available_capability",
                runtime_state="available",
                source="fixture",
                evidence_ref="fresh-available",
            ),
            RuntimeCapabilityEvidence(
                capability_id="available_capability",
                runtime_state="degraded",
                source="fixture",
                evidence_ref="fresh-degraded",
            ),
            RuntimeCapabilityEvidence(
                capability_id="available_capability",
                runtime_state="unavailable",
                source="fixture",
                evidence_ref="fresh-unavailable",
            ),
        ],
        now="2026-10-02T00:00:00Z",
    )

    available = next(
        item
        for item in truth["capabilities"]
        if item["capability_id"] == "available_capability"
    )

    assert available["accepted_runtime_states"] == [
        "available",
        "degraded",
        "unavailable",
    ]
    assert available["derived_runtime_state"] == "unavailable"
    assert available["live_runtime_execution"] is False
    assert available["registry_runtime_relation"] == "runtime_conflicts_with_registry"
    assert "RUNTIME_CONFLICTS_WITH_REGISTRY" in available["reason_codes"]


def test_time_expired_runtime_evidence_cannot_be_accepted_for_truth() -> None:
    truth = reconcile_capability_runtime_truth(
        registry(),
        [
            RuntimeCapabilityEvidence(
                capability_id="planned_capability",
                runtime_state="available",
                source="fixture",
                evidence_ref="expired",
                observed_at="2026-10-01T00:00:00Z",
                expires_at="2026-10-01T01:00:00Z",
            )
        ],
        now="2026-10-02T00:00:00Z",
    )

    planned = next(
        item
        for item in truth["capabilities"]
        if item["capability_id"] == "planned_capability"
    )

    assert planned["derived_runtime_state"] == "unavailable"
    assert planned["runtime_evidence_state"] == "stale_runtime_evidence"
    assert "RUNTIME_EVIDENCE_EXPIRED" in planned["reason_codes"]
    assert planned["runtime_evidence"][0]["time_current"] is False
    assert planned["runtime_evidence"][0]["accepted_for_truth"] is False


def test_legacy_and_superseded_registry_statuses_are_declared_unavailable() -> None:
    source = {
        "version": "1.0.0",
        "capabilities": {
            "old": {"status": "LEGACY", "module": "old.py", "tested": True},
            "newer": {"status": "SUPERSEDED", "module": "newer.py", "tested": True},
        },
    }

    truth = reconcile_capability_runtime_truth(source, now="2026-10-02T00:00:00Z")
    by_id = {item["capability_id"]: item for item in truth["capabilities"]}

    assert by_id["old"]["registry_lifecycle"] == "legacy"
    assert by_id["old"]["registry_declared_state"] == "declared_unavailable"
    assert "LEGACY_CAPABILITY" in by_id["old"]["reason_codes"]
    assert by_id["newer"]["registry_lifecycle"] == "superseded"
    assert by_id["newer"]["registry_declared_state"] == "declared_unavailable"
    assert "SUPERSEDED_CAPABILITY" in by_id["newer"]["reason_codes"]


def test_source_state_and_interface_usability_require_accepted_runtime_truth(
    tmp_path: Path,
) -> None:
    (tmp_path / "present.py").write_text("# fixture\n", encoding="utf-8")
    source = {
        "version": "1.0.0",
        "capabilities": {
            "present": {"status": "available", "module": "present.py", "tested": True},
            "missing": {"status": "available", "module": "missing.py", "tested": True},
        },
    }

    truth = reconcile_capability_runtime_truth(
        source,
        source_root=tmp_path,
        now="2026-10-02T00:00:00Z",
    )
    by_id = {item["capability_id"]: item for item in truth["capabilities"]}

    assert by_id["present"]["source_state"] == "present"
    assert by_id["present"]["interface_usable"] is False
    assert by_id["missing"]["source_state"] == "missing"
    assert by_id["missing"]["interface_usable"] is False
    assert "REGISTRY_SOURCE_MISSING" in by_id["missing"]["reason_codes"]

    verified_truth = reconcile_capability_runtime_truth(
        source,
        [
            RuntimeCapabilityEvidence(
                capability_id="present",
                runtime_state="available",
                source="fixture",
                evidence_ref="typed",
            )
        ],
        source_root=tmp_path,
        now="2026-10-02T00:00:00Z",
    )
    verified_by_id = {
        item["capability_id"]: item for item in verified_truth["capabilities"]
    }

    assert verified_by_id["present"]["interface_usable"] is True
    assert verified_by_id["missing"]["interface_usable"] is False


def test_reconciler_rejects_untyped_evidence() -> None:
    with pytest.raises(TypeError, match="RuntimeCapabilityEvidence"):
        reconcile_capability_runtime_truth(
            registry(),
            [
                {
                    "capability_id": "available_capability",
                    "runtime_state": "available",
                }
            ],
        )


def test_reconciler_rejects_unknown_capability_evidence() -> None:
    with pytest.raises(ValueError, match="unknown registry capabilities"):
        reconcile_capability_runtime_truth(
            registry(),
            [
                RuntimeCapabilityEvidence(
                    capability_id="missing",
                    runtime_state="available",
                    source="fixture",
                    evidence_ref="typed",
                )
            ],
        )


def test_capability_runtime_truth_schema_validates_report() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    truth = reconcile_capability_runtime_truth(registry())

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(truth, schema)
