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
        "registry_available_with_runtime_evidence_count": 0,
    }
    assert {
        item["truth_status"] for item in truth["capabilities"]
    } == {"registry_authoritative_no_runtime_evidence"}


def test_available_runtime_evidence_confirms_only_registry_available_capability() -> None:
    truth = reconcile_capability_runtime_truth(
        registry(),
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

    statuses = {item["capability_id"]: item["truth_status"] for item in truth["capabilities"]}

    assert statuses["available_capability"] == "registry_available_runtime_confirmed"
    assert statuses["planned_capability"] == "registry_authoritative_runtime_evidence_ignored"


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
