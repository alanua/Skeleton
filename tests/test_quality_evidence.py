from __future__ import annotations

import pytest

from core.quality_evidence import (
    DependencyEvidence,
    EvidenceStrength,
    RuntimeEvidenceState,
    evidence_bundle_from_mapping,
    evidence_receipt,
)


HEAD = "a" * 40


def test_mock_only_evidence_cannot_satisfy_production_contract_requirement() -> None:
    bundle = evidence_bundle_from_mapping(
        {
            "tests": {
                "tests_passed": True,
                "diff_check_passed": True,
                "compile_passed": True,
                "strength": "MOCK_ONLY",
                "production_contract_required": True,
                "bound_head_sha": HEAD,
            }
        }
    )

    assert bundle.tests is not None
    assert bundle.tests.strength is EvidenceStrength.MOCK_ONLY
    assert not bundle.tests.is_green


def test_dependency_existence_evidence_reports_missing_dependencies() -> None:
    evidence = DependencyEvidence(
        declared_dependencies=("pytest", "requests"),
        existing_dependencies=("pytest",),
    )

    assert evidence.missing_dependencies == ("requests",)
    assert not evidence.is_satisfied


def test_runtime_evidence_state_requires_post_runtime_value() -> None:
    bundle = evidence_bundle_from_mapping(
        {"runtime": {"state": "PRE_MERGE_ONLY", "bound_head_sha": HEAD}}
    )

    assert bundle.runtime is not None
    assert bundle.runtime.state is RuntimeEvidenceState.PRE_MERGE_ONLY
    assert not bundle.runtime.is_runtime_proven


def test_malformed_evidence_fails_closed_with_stable_reason_code() -> None:
    with pytest.raises(ValueError, match="INVALID_TEST_EVIDENCE"):
        evidence_bundle_from_mapping({"tests": {"tests_passed": "yes"}})


def test_public_receipt_contains_only_public_safe_shapes() -> None:
    bundle = evidence_bundle_from_mapping(
        {
            "tests": {
                "tests_passed": True,
                "diff_check_passed": True,
                "compile_passed": True,
                "strength": "PRODUCTION_CONTRACT",
            },
            "dependencies": {
                "declared_dependencies": ["private-package-name"],
                "existing_dependencies": ["private-package-name"],
            },
            "review": {"independent_review": True, "adversarial_review": True},
            "runtime": {"state": "NOT_PROVIDED"},
        }
    )

    receipt = evidence_receipt(
        reason_codes=("OK",),
        tests=bundle.tests,
        dependencies=bundle.dependencies,
        review=bundle.review,
        runtime=bundle.runtime,
    )

    assert receipt["dependency_count"] == 1
    assert receipt["dependency_set_hash"] != "private-package-name"
    assert "private-package-name" not in str(receipt)
