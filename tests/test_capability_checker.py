from __future__ import annotations

from pathlib import Path

from core.capability_checker import CapabilityChecker
from core.capability_runtime_truth import RuntimeCapabilityEvidence


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "CAPABILITY_REGISTRY.yaml"


def test_capability_checker_runtime_truth_is_bounded_read_only() -> None:
    truth = CapabilityChecker(REGISTRY_PATH).runtime_truth()

    assert truth["schema"] == "skeleton.capability_runtime_truth.v1"
    assert truth["registry_authority"] == "CAPABILITY_REGISTRY.yaml"
    assert truth["read_model"] == "bounded_typed_evidence_only"
    assert truth["runtime_probe_performed"] is False
    assert truth["runtime_mutation_performed"] is False
    assert truth["summary"]["typed_runtime_evidence_count"] == 0
    assert truth["summary"]["accepted_runtime_evidence_count"] == 0


def test_capability_checker_runtime_truth_accepts_typed_evidence() -> None:
    evidence = RuntimeCapabilityEvidence(
        capability_id="boot_loader",
        runtime_state="available",
        source="unit_test",
        evidence_ref="test_capability_checker",
    )

    truth = CapabilityChecker(REGISTRY_PATH).runtime_truth([evidence])
    boot_loader = next(
        item for item in truth["capabilities"] if item["capability_id"] == "boot_loader"
    )

    assert boot_loader["registry_status"] == "available"
    assert boot_loader["runtime_states"] == ["available"]
    assert boot_loader["accepted_runtime_states"] == ["available"]
    assert boot_loader["derived_runtime_state"] == "available"
    assert boot_loader["runtime_evidence_state"] == "accepted_runtime_evidence"
    assert boot_loader["registry_runtime_relation"] == "runtime_matches_registry"


def test_capability_checker_runtime_truth_accepts_deterministic_clock() -> None:
    truth = CapabilityChecker(REGISTRY_PATH).runtime_truth(
        now="2026-10-02T00:00:00Z",
    )

    assert truth["checked_at"] == "2026-10-02T00:00:00Z"
