from __future__ import annotations

from pathlib import Path

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


def test_capability_checker_source_implemented_tool_needs_same_chat_fresh_evidence() -> None:
    checker = CapabilityChecker(REGISTRY_PATH)
    evidence = RuntimeCapabilityEvidence(
        capability_id="capability_runtime_truth",
        runtime_state="available",
        source="synthetic_chat_alpha",
        evidence_ref="registered_tool:chat-alpha/reconcile_capability_runtime_truth",
        observed_at="2026-10-01T00:00:00Z",
        runtime_bound=True,
        source_runtime_parity=True,
        usable_interfaces=(
            "registered_tool:chat-alpha/reconcile_capability_runtime_truth",
        ),
        evidence_kind="registered_tool",
    )

    chat_alpha_truth = checker.runtime_truth([evidence], now=NOW)
    chat_beta_truth = checker.runtime_truth(now=NOW)
    chat_alpha = next(
        item
        for item in chat_alpha_truth["capabilities"]
        if item["capability_id"] == "capability_runtime_truth"
    )
    chat_beta = next(
        item
        for item in chat_beta_truth["capabilities"]
        if item["capability_id"] == "capability_runtime_truth"
    )

    assert chat_alpha["declared_status"] == "source_implemented"
    assert chat_alpha["effective_status"] == "LIVE"
    assert chat_alpha["usable_interfaces"] == [
        "registered_tool:chat-alpha/reconcile_capability_runtime_truth"
    ]
    assert chat_alpha["authoritative"] is False
    assert chat_beta["effective_status"] == "CONTRACT_ONLY"
    assert chat_beta["runtime_evidence"] == []
    assert "active" not in chat_beta
    assert "installed" not in chat_beta
