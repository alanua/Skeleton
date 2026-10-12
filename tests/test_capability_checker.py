from __future__ import annotations

from pathlib import Path

import pytest

from core.capability_checker import CapabilityChecker
from core.capability_runtime_truth import RuntimeCapabilityEvidence


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "CAPABILITY_REGISTRY.yaml"
NOW = "2026-10-02T00:00:00Z"


def test_capability_checker_runtime_truth_is_bounded_read_only() -> None:
    truth = CapabilityChecker(REGISTRY_PATH).runtime_truth()

    assert truth["schema"] == "skeleton.capability_runtime_truth.v1"
    assert truth["registry_authority"] == "CAPABILITY_REGISTRY.yaml"
    assert truth["read_model"] == "bounded_typed_evidence_only"
    assert truth["runtime_probe_performed"] is False
    assert truth["runtime_mutation_performed"] is False
    assert truth["summary"]["typed_runtime_evidence_count"] == 0
    assert truth["summary"]["fresh_runtime_evidence_count"] == 0


def test_capability_checker_runtime_truth_accepts_typed_evidence() -> None:
    evidence = RuntimeCapabilityEvidence(
        capability_id="boot_loader",
        runtime_state="available",
        source="unit_test",
        evidence_ref="test_capability_checker",
        observed_at="2026-10-01T00:00:00Z",
        runtime_bound=True,
        source_runtime_parity=True,
        usable_interfaces=("BootLoader.load",),
    )

    truth = CapabilityChecker(REGISTRY_PATH).runtime_truth([evidence], now=NOW)
    boot_loader = next(
        item for item in truth["capabilities"] if item["capability_id"] == "boot_loader"
    )

    assert boot_loader["declared_status"] == "available"
    assert boot_loader["effective_status"] == "LIVE"
    assert boot_loader["freshness"] == "FRESH"
    assert boot_loader["runtime_bound"] is True
    assert boot_loader["source_runtime_parity"] is True
    assert boot_loader["usable_interfaces"] == ["BootLoader.load"]


def test_capability_checker_runtime_truth_accepts_deterministic_clock() -> None:
    truth = CapabilityChecker(REGISTRY_PATH).runtime_truth(
        now="2026-10-02T00:00:00Z",
    )

    assert truth["checked_at"] == "2026-10-02T00:00:00Z"


def test_capability_checker_registry_available_without_evidence_is_not_live() -> None:
    truth = CapabilityChecker(REGISTRY_PATH).runtime_truth(now=NOW)
    boot_loader = next(
        item for item in truth["capabilities"] if item["capability_id"] == "boot_loader"
    )

    assert boot_loader["declared_status"] == "available"
    assert boot_loader["effective_status"] != "LIVE"


def test_capability_checker_global_registered_tool_lookup_is_exact() -> None:
    checker = CapabilityChecker(REGISTRY_PATH)
    capability = checker.load()["capabilities"]["capability_runtime_truth"]
    truth = checker.runtime_truth(now=NOW)
    runtime_truth = next(
        item
        for item in truth["capabilities"]
        if item["capability_id"] == "capability_runtime_truth"
    )

    assert capability["module"] == "core/capability_runtime_truth.py"
    assert capability["entry"] == "reconcile_capability_runtime_truth"
    assert capability["live_runtime_execution"] is False
    assert checker.is_available("capability_runtime_truth") is False
    assert checker.is_available("capability-runtime-truth") is False
    assert runtime_truth["declared_status"] == "source_implemented"
    assert runtime_truth["effective_status"] == "CONTRACT_ONLY"


def test_capability_checker_runtime_evidence_identity_mismatch_is_rejected() -> None:
    evidence = RuntimeCapabilityEvidence(
        capability_id="capability_runtime_truth_typo",
        runtime_state="available",
        source="unit_test",
        evidence_ref="test_capability_checker",
        observed_at="2026-10-01T00:00:00Z",
        runtime_bound=True,
        source_runtime_parity=True,
        usable_interfaces=("reconcile_capability_runtime_truth",),
    )

    with pytest.raises(ValueError, match="unknown registry capabilities"):
        CapabilityChecker(REGISTRY_PATH).runtime_truth([evidence], now=NOW)


def test_capability_checker_unavailable_runtime_evidence_is_blocked_not_live() -> None:
    evidence = RuntimeCapabilityEvidence(
        capability_id="boot_loader",
        runtime_state="unavailable",
        source="unit_test",
        evidence_ref="test_capability_checker",
        observed_at="2026-10-01T00:00:00Z",
        runtime_bound=True,
        source_runtime_parity=True,
        usable_interfaces=("BootLoader.load",),
    )

    truth = CapabilityChecker(REGISTRY_PATH).runtime_truth([evidence], now=NOW)
    boot_loader = next(
        item for item in truth["capabilities"] if item["capability_id"] == "boot_loader"
    )

    assert boot_loader["declared_status"] == "available"
    assert boot_loader["effective_status"] == "BLOCKED"
    assert "RUNTIME_UNAVAILABLE" in boot_loader["reason_codes"]
    assert truth["summary"]["live_count"] == 0
    assert truth["summary"]["blocked_count"] == 1


def test_capability_checker_static_registry_entry_cannot_fallback_to_live() -> None:
    truth = CapabilityChecker(REGISTRY_PATH).runtime_truth(now=NOW)
    runtime_truth = next(
        item
        for item in truth["capabilities"]
        if item["capability_id"] == "capability_runtime_truth"
    )

    assert runtime_truth["declared_status"] == "source_implemented"
    assert runtime_truth["freshness"] == "UNKNOWN"
    assert runtime_truth["runtime_bound"] is False
    assert runtime_truth["source_runtime_parity"] is False
    assert runtime_truth["usable_interfaces"] == []
    assert runtime_truth["effective_status"] != "LIVE"
