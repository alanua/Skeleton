from __future__ import annotations

from core.quality_evidence import (
    ARCHITECTURE_REVIEW_REQUIRED,
    CALLER_PROOF_REJECTED,
    OBSERVED_DIFF_UNREACHED,
    PRODUCTION_CONTRACT_REVIEW_REQUIRED,
    RUNTIME_REVIEW_UNREACHED,
    evaluate_phase1_evidence,
)


def test_architecture_required_never_accepts_caller_receipt_as_green() -> None:
    decision = evaluate_phase1_evidence(
        {
            "architecture_required": True,
            "architecture_receipt": {"state": "ARCHITECTURE_GREEN"},
        }
    )

    assert decision.architecture_status == ARCHITECTURE_REVIEW_REQUIRED
    assert decision.caller_proof_status == CALLER_PROOF_REJECTED
    assert decision.rejected_fields == ("architecture_receipt",)
    assert decision.rejected_states == ("ARCHITECTURE_GREEN",)


def test_production_contract_required_never_accepts_caller_receipt_as_green() -> None:
    decision = evaluate_phase1_evidence(
        {
            "production_contract_required": True,
            "production_contract_receipt": {
                "state": "PRODUCTION_CONTRACT_GREEN",
            },
        }
    )

    assert decision.production_contract_status == PRODUCTION_CONTRACT_REVIEW_REQUIRED
    assert decision.caller_proof_status == CALLER_PROOF_REJECTED
    assert decision.rejected_fields == ("production_contract_receipt",)


def test_runtime_proven_and_observed_diff_are_unreachable_in_phase1() -> None:
    decision = evaluate_phase1_evidence(
        {
            "runtime_state": "RUNTIME_PROVEN",
            "observed_diff_impact": {"state": "OBSERVED_DIFF_IMPACT_PROVEN"},
            "touched_files": ["core/task_quality_gate.py"],
        }
    )

    assert decision.runtime_status == RUNTIME_REVIEW_UNREACHED
    assert decision.observed_diff_status == OBSERVED_DIFF_UNREACHED
    assert decision.caller_proof_status == CALLER_PROOF_REJECTED
    assert decision.rejected_fields == (
        "observed_diff_impact",
        "runtime_state",
        "touched_files",
    )
    assert "RUNTIME_PROVEN" in decision.rejected_states


def test_plain_claim_side_validation_metadata_remains_claim_side_only() -> None:
    decision = evaluate_phase1_evidence(
        {
            "operation": "repair_phase1",
            "architecture_required": True,
            "production_contract_required": True,
        }
    )

    assert decision.accepted
    assert decision.architecture_status == ARCHITECTURE_REVIEW_REQUIRED
    assert decision.production_contract_status == PRODUCTION_CONTRACT_REVIEW_REQUIRED
