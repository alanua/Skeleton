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
    truth = reconcile_capability_runtime_truth(registry())

    assert truth["runtime_probe_performed"] is False
    assert truth["runtime_mutation_performed"] is False
    assert truth["summary"] == {
        "registry_capability_count": 2,
        "registry_available_count": 1,
        "typed_runtime_evidence_count": 0,
        "fresh_verified_runtime_evidence_count": 0,
        "effective_available_count": 1,
        "drift_count": 0,
        "parity_match_count": 0,
    }
    assert {item["freshness"] for item in truth["capabilities"]} == {
        "no_runtime_evidence"
    }
    assert {item["drift"] for item in truth["capabilities"]} == {
        "not_evaluated_without_fresh_evidence"
    }
    assert {item["parity"] for item in truth["capabilities"]} == {"registry_only"}


def test_fresh_verified_runtime_evidence_derives_effective_state_without_mutating_registry() -> None:
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

    assert capabilities["available_capability"]["effective_state"] == "available"
    assert capabilities["available_capability"]["drift"] == "no_drift"
    assert capabilities["available_capability"]["parity"] == "registry_runtime_parity"
    assert capabilities["planned_capability"]["registry_status"] == "planned"
    assert capabilities["planned_capability"]["registry_effective_state"] == "unavailable"
    assert capabilities["planned_capability"]["effective_state"] == "available"
    assert capabilities["planned_capability"]["drift"] == "runtime_overrides_stale_registry"
    assert capabilities["planned_capability"]["parity"] == "registry_runtime_drift"
    assert source_registry["capabilities"]["planned_capability"]["status"] == "planned"


def test_stale_or_unverified_runtime_evidence_cannot_override_registry() -> None:
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

    assert capabilities["planned_capability"]["effective_state"] == "unavailable"
    assert capabilities["planned_capability"]["freshness"] == (
        "stale_or_unverified_runtime_evidence"
    )
    assert capabilities["planned_capability"]["parity"] == "registry_runtime_drift"
    assert capabilities["available_capability"]["effective_state"] == "available"


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
