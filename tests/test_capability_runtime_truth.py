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
NOW = "2026-10-02T00:00:00Z"
OBSERVED = "2026-10-01T00:00:00Z"
OLD_PUBLIC_FIELDS = {
    "derived_runtime_state",
    "runtime_evidence_state",
    "registry_runtime_relation",
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


def evidence(
    capability_id: str,
    runtime_state: str = "available",
    **overrides: object,
) -> RuntimeCapabilityEvidence:
    payload = {
        "capability_id": capability_id,
        "runtime_state": runtime_state,
        "source": "fixture",
        "evidence_ref": capability_id,
        "observed_at": OBSERVED,
    }
    payload.update(overrides)
    return RuntimeCapabilityEvidence(**payload)


def by_id(truth: dict) -> dict[str, dict]:
    return {item["capability_id"]: item for item in truth["capabilities"]}


def test_reconciler_exposes_exact_public_contract_without_evidence() -> None:
    truth = reconcile_capability_runtime_truth(registry(), now=NOW)
    capabilities = by_id(truth)

    assert truth["runtime_probe_performed"] is False
    assert truth["runtime_mutation_performed"] is False
    assert truth["checked_at"] == NOW
    assert truth["summary"] == {
        "registry_capability_count": 2,
        "registry_available_count": 1,
        "typed_runtime_evidence_count": 0,
        "fresh_runtime_evidence_count": 0,
        "live_count": 0,
        "partial_count": 0,
        "contract_only_count": 2,
        "blocked_count": 0,
        "legacy_count": 0,
        "superseded_count": 0,
        "drift_count": 0,
    }
    assert {item["effective_status"] for item in capabilities.values()} == {
        "CONTRACT_ONLY"
    }
    assert {item["freshness"] for item in capabilities.values()} == {"UNKNOWN"}
    assert capabilities["available_capability"]["effective_status"] != "LIVE"
    assert all(
        item["authoritative"] is False and item["public_safe"] is True
        for item in capabilities.values()
    )
    assert all(not (OLD_PUBLIC_FIELDS & item.keys()) for item in capabilities.values())


def test_required_enums_are_literally_asserted() -> None:
    source = {
        "version": "1.0.0",
        "capabilities": {
            "live": {"status": "available", "module": "live.py", "tested": True},
            "partial": {"status": "available", "module": "partial.py", "tested": True},
            "contract": {"status": "available", "module": "contract.py", "tested": True},
            "blocked": {"status": "available", "module": "blocked.py", "tested": True},
            "legacy": {"status": "available", "module": "legacy.py", "tested": True},
            "superseded": {
                "status": "available",
                "module": "superseded.py",
                "tested": True,
            },
            "stale": {"status": "available", "module": "stale.py", "tested": True},
        },
    }
    truth = reconcile_capability_runtime_truth(
        source,
        [
            evidence(
                "live",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
                evidence_kind="probe",
            ),
            evidence("partial", runtime_bound=True, source_runtime_parity=False),
            evidence("blocked", "unavailable", runtime_bound=True),
            evidence("legacy", lifecycle_hint="legacy", runtime_bound=True),
            evidence("superseded", lifecycle_hint="superseded", runtime_bound=True),
            evidence(
                "stale",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
                expires_at="2026-10-01T01:00:00Z",
            ),
        ],
        now=NOW,
    )
    capabilities = by_id(truth)

    assert {
        capabilities["live"]["effective_status"],
        capabilities["partial"]["effective_status"],
        capabilities["contract"]["effective_status"],
        capabilities["blocked"]["effective_status"],
        capabilities["legacy"]["effective_status"],
        capabilities["superseded"]["effective_status"],
    } == {
        "LIVE",
        "PARTIAL",
        "CONTRACT_ONLY",
        "BLOCKED",
        "LEGACY",
        "SUPERSEDED",
    }
    assert {
        capabilities["live"]["freshness"],
        capabilities["contract"]["freshness"],
        capabilities["stale"]["freshness"],
    } == {"FRESH", "UNKNOWN", "STALE"}
    assert capabilities["stale"]["effective_status"] != "LIVE"
    assert capabilities["live"]["evidence_observed_at"] == OBSERVED
    assert capabilities["live"]["evidence_kinds"] == ["probe"]


def test_truth_rules_prevent_false_live_results() -> None:
    source = {
        "version": "1.0.0",
        "capabilities": {
            "stale": {"status": "available", "module": "stale.py", "tested": True},
            "parity": {"status": "available", "module": "parity.py", "tested": True},
            "bound": {"status": "available", "module": "bound.py", "tested": True},
            "unbound_unavailable": {
                "status": "available",
                "module": "unbound_unavailable.py",
                "tested": True,
            },
            "conflict": {
                "status": "available",
                "module": "conflict.py",
                "tested": True,
            },
        },
    }
    truth = reconcile_capability_runtime_truth(
        source,
        [
            evidence(
                "stale",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
                expires_at="2026-10-01T01:00:00Z",
            ),
            evidence(
                "parity",
                runtime_bound=True,
                source_runtime_parity=False,
                usable_interfaces=("entrypoint",),
            ),
            evidence(
                "bound",
                runtime_bound=False,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
            ),
            evidence("unbound_unavailable", "unavailable", runtime_bound=False),
            evidence(
                "conflict",
                "available",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
            ),
            evidence("conflict", "degraded", runtime_bound=True),
        ],
        now=NOW,
    )
    capabilities = by_id(truth)

    assert capabilities["stale"]["freshness"] == "STALE"
    assert capabilities["stale"]["effective_status"] != "LIVE"
    assert capabilities["parity"]["source_runtime_parity"] is False
    assert capabilities["parity"]["effective_status"] != "LIVE"
    assert capabilities["bound"]["runtime_bound"] is False
    assert capabilities["bound"]["effective_status"] != "LIVE"
    assert capabilities["unbound_unavailable"]["effective_status"] == "PARTIAL"
    assert capabilities["conflict"]["effective_status"] == "PARTIAL"


def test_fresh_verified_runtime_overrides_stale_registry_text_with_drift() -> None:
    truth = reconcile_capability_runtime_truth(
        registry(),
        [
            evidence(
                "planned_capability",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
            )
        ],
        now=NOW,
    )
    planned = by_id(truth)["planned_capability"]

    assert planned["declared_status"] == "planned"
    assert planned["effective_status"] == "LIVE"
    assert planned["drift"] is True
    assert planned["runtime_bound"] is True
    assert planned["source_present"] is True
    assert planned["source_runtime_parity"] is True
    assert planned["usable_interfaces"] == ["entrypoint"]


def test_future_observed_at_fails_closed_as_stale() -> None:
    truth = reconcile_capability_runtime_truth(
        registry(),
        [
            evidence(
                "available_capability",
                observed_at="2026-10-03T00:00:00Z",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
            )
        ],
        now=NOW,
    )
    available = by_id(truth)["available_capability"]

    assert available["freshness"] == "STALE"
    assert available["effective_status"] != "LIVE"


def test_malformed_and_unknown_evidence_raise_before_output() -> None:
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
    with pytest.raises(ValueError, match="observed_at"):
        RuntimeCapabilityEvidence(
            capability_id="available_capability",
            runtime_state="available",
            source="fixture",
            evidence_ref="bad",
            observed_at="",
        )
    with pytest.raises(ValueError, match="unknown registry capabilities"):
        reconcile_capability_runtime_truth(
            registry(),
            [evidence("missing")],
            now=NOW,
        )


def test_capability_runtime_truth_schema_validates_report() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    truth = reconcile_capability_runtime_truth(
        registry(),
        [
            evidence(
                "available_capability",
                runtime_bound=True,
                source_runtime_parity=True,
                usable_interfaces=("entrypoint",),
            )
        ],
        now=NOW,
    )

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(truth, schema)
