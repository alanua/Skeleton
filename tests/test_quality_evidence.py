from __future__ import annotations

import pytest

from core.quality_evidence import (
    POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1,
    RUNTIME_PROVEN,
    EvidenceValidationError,
    HeadBoundTestEvidence,
    MockProductionContractEvidence,
    ProductionContractPreMergePlaceholder,
    ReadinessReceipt,
    stable_hash,
)


BASE = "47320dab7740b6c26d006e1b6e3e8d23cd7bcca5"
HEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def valid_test_evidence() -> dict[str, object]:
    return {
        "base_sha": BASE,
        "head_sha": HEAD,
        "passed": True,
        "total_tests": 12,
        "failed_tests": 0,
        "commands": ["python3 -m pytest -q"],
        "evidence_hash": "b" * 64,
    }


def reason(mapping: dict[str, object]) -> str:
    with pytest.raises(EvidenceValidationError) as excinfo:
        HeadBoundTestEvidence.from_mapping(mapping)
    return excinfo.value.reason_code


def test_head_bound_test_evidence_normalizes_public_test_totals() -> None:
    evidence = HeadBoundTestEvidence.from_mapping(valid_test_evidence())

    assert evidence.base_sha == BASE
    assert evidence.head_sha == HEAD
    assert evidence.passed is True
    assert evidence.total_tests == 12
    assert evidence.commands == ("python3 -m pytest -q",)


def test_malformed_test_evidence_fails_closed_with_stable_reasons() -> None:
    mapping = valid_test_evidence()
    mapping["head_sha"] = "abc"
    assert reason(mapping) == "INVALID_HEAD_SHA"
    mapping = valid_test_evidence()
    mapping["failed_tests"] = 1
    assert reason(mapping) == "CONTRADICTORY_TEST_EVIDENCE"
    mapping = valid_test_evidence()
    mapping["commands"] = []
    assert reason(mapping) == "INVALID_TEST_COMMANDS"


def test_production_contract_placeholder_is_explicitly_unauthenticated() -> None:
    placeholder = ProductionContractPreMergePlaceholder(evidence_hash="c" * 64)

    assert placeholder.authenticated is False
    with pytest.raises(EvidenceValidationError) as excinfo:
        ProductionContractPreMergePlaceholder(
            evidence_hash="c" * 64,
            authenticated=True,
        )
    assert excinfo.value.reason_code == (
        "AUTHENTIC_PRODUCTION_CONTRACT_NOT_VERIFIABLE_IN_PHASE1"
    )


def test_mock_only_production_contract_evidence_stays_mock_only() -> None:
    evidence = MockProductionContractEvidence(evidence_hash="d" * 64)

    assert evidence.mock_only is True


def test_readiness_receipt_cannot_emit_runtime_proven() -> None:
    with pytest.raises(EvidenceValidationError) as excinfo:
        ReadinessReceipt(
            state=RUNTIME_PROVEN,
            reason_codes=(),
            task_spec_valid=True,
            tests_green=True,
            architecture_required=False,
            production_contract_required=False,
            runtime_proven=True,
            scope_classification="non-protected-phase1-scope",
            privacy_boundary="PUBLIC_SAFE_POLICY_METADATA_ONLY",
            declared_allowed_files_count=1,
            requested_capability_count=1,
            validation_requirement_count=1,
            declared_allowed_files_hash=stable_hash(("core/task_quality_gate.py",)),
            requested_capabilities_hash=stable_hash(("repository_read",)),
            validation_requirements_hash=stable_hash(("pytest",)),
            base_sha=BASE,
            head_sha=HEAD,
            idempotency_key="idempotency-key",
        )

    assert excinfo.value.reason_code == "INVALID_READINESS_STATE"


def test_public_receipt_contains_no_declared_file_names_or_observed_diff_surface() -> None:
    receipt = ReadinessReceipt(
        state="TESTS_GREEN",
        reason_codes=(POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1,),
        task_spec_valid=True,
        tests_green=True,
        architecture_required=False,
        production_contract_required=False,
        runtime_proven=False,
        scope_classification="non-protected-phase1-scope",
        privacy_boundary="PUBLIC_SAFE_POLICY_METADATA_ONLY",
        declared_allowed_files_count=1,
        requested_capability_count=1,
        validation_requirement_count=1,
        declared_allowed_files_hash=stable_hash(("core/task_quality_gate.py",)),
        requested_capabilities_hash=stable_hash(("repository_read",)),
        validation_requirements_hash=stable_hash(("pytest",)),
        base_sha=BASE,
        head_sha=HEAD,
        idempotency_key="idempotency-key",
    )

    public = receipt.to_public_mapping()

    assert "allowed_files" not in public
    assert "touched_files" not in public
    assert "ObservedDiffImpact" not in public
    assert public["runtime_proven"] is False
