from __future__ import annotations

import pytest

from core.quality_evidence import (
    ARCHITECTURE_REVIEW_REQUIRED,
    PRODUCTION_CONTRACT_REVIEW_REQUIRED,
    PROTECTED_REVIEW_REQUIRED,
    PUBLIC_REVIEW_ALLOWED,
    QualityEvidenceError,
    assert_no_phase1_proof_claims,
    classify_phase1_evidence,
    public_evidence_mapping,
)


def test_architecture_and_production_requirements_remain_unmet_in_phase1() -> None:
    classification = classify_phase1_evidence(
        architecture_required=True,
        production_contract_required=True,
        protected_scope_declared=False,
        protected_intent=False,
        privacy_boundary="PUBLIC_SAFE_POLICY_METADATA_ONLY",
        risk="LOW",
    )

    assert classification.architecture_status == ARCHITECTURE_REVIEW_REQUIRED
    assert classification.production_contract_status == PRODUCTION_CONTRACT_REVIEW_REQUIRED
    assert classification.review_requirements == (
        ARCHITECTURE_REVIEW_REQUIRED,
        PRODUCTION_CONTRACT_REVIEW_REQUIRED,
    )


def test_absent_later_phase_receipts_do_not_block_when_not_required() -> None:
    classification = classify_phase1_evidence(
        architecture_required=False,
        production_contract_required=False,
        protected_scope_declared=False,
        protected_intent=False,
        privacy_boundary="PUBLIC_SAFE_POLICY_METADATA_ONLY",
        risk="LOW",
    )

    assert classification.architecture_status is None
    assert classification.production_contract_status is None
    assert classification.protected_status == PUBLIC_REVIEW_ALLOWED
    assert classification.review_requirements == ()


@pytest.mark.parametrize(
    "claim",
    [
        "ARCHITECTURE_GREEN",
        "RUNTIME_PROVEN",
        {"state": "ARCHITECTURE_GREEN"},
        {"review": {"runtime_state": "RUNTIME_PROVEN"}},
        {"ARCHITECTURE_GREEN": {"reviewer_id": "human"}},
        ["safe", {"state": "RUNTIME_PROVEN"}],
    ],
)
def test_phase1_rejects_caller_proof_claims_anywhere(claim: object) -> None:
    with pytest.raises(QualityEvidenceError, match="caller-supplied"):
        assert_no_phase1_proof_claims(claim)


def test_private_boundary_and_protected_risk_cannot_be_downgraded() -> None:
    classification = classify_phase1_evidence(
        architecture_required=False,
        production_contract_required=False,
        protected_scope_declared=False,
        protected_intent=False,
        privacy_boundary="PUBLIC_SAFE_POLICY_METADATA_ONLY+PRIVATE_RUNTIME_CONTEXT",
        risk="CRITICAL",
    )

    assert classification.protected_status == PROTECTED_REVIEW_REQUIRED
    assert "private or composite privacy boundary" in classification.reasons
    assert "protected risk level" in classification.reasons


def test_public_mapping_contains_only_policy_metadata() -> None:
    classification = classify_phase1_evidence(
        architecture_required=False,
        production_contract_required=False,
        protected_scope_declared=True,
        protected_intent=False,
        privacy_boundary="PUBLIC_SAFE_POLICY_METADATA_ONLY",
        risk="LOW",
    )

    public = public_evidence_mapping(classification)

    assert public["protected_status"] == PROTECTED_REVIEW_REQUIRED
    assert "touched_files" not in public
    assert "ObservedDiffImpact" not in public
