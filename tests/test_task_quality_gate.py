from __future__ import annotations

import pytest

from core.architecture_invariants import ArchitectureInvariantEvidence
from core.quality_evidence import (
    DependencyEvidence,
    EvidenceStrength,
    ReviewEvidence,
    RuntimeEvidence,
    RuntimeEvidenceState,
    TestEvidence as QualityTestEvidence,
)
from core.task_quality_gate import (
    NormalizedTaskContract,
    ReadinessState,
    TaskQualityEvidence,
    evaluate_task_quality,
    normalize_task_contract,
)


BASE = "1" * 40
HEAD = "2" * 40


def contract(**overrides: object) -> NormalizedTaskContract:
    values: dict[str, object] = {
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "branch": "runner/example",
        "base_sha": BASE,
        "current_head_sha": HEAD,
        "task_kind": "code_generation",
        "declared_risk": "green",
        "protected_target_declared": False,
        "requested_capabilities": ("repository_read", "repository_write", "test_execution"),
        "allowed_files": ("core/task_quality_gate.py", "tests/test_task_quality_gate.py"),
        "validation_commands_count": 3,
        "expected_output_count": 2,
    }
    values.update(overrides)
    return normalize_task_contract(values)


def make_test_evidence(
    strength: EvidenceStrength = EvidenceStrength.STATIC_CONTRACT,
) -> QualityTestEvidence:
    return QualityTestEvidence(
        tests_passed=True,
        diff_check_passed=True,
        compile_passed=True,
        strength=strength,
        bound_head_sha=HEAD,
    )


def dependencies() -> DependencyEvidence:
    return DependencyEvidence(
        declared_dependencies=("pytest",),
        existing_dependencies=("pytest",),
    )


def architecture(**overrides: object) -> ArchitectureInvariantEvidence:
    values = {
        "invariants_checked": True,
        "immutable_invariants_preserved": True,
        "dependency_boundaries_preserved": True,
        "capability_surface_reviewed": True,
        "bound_head_sha": HEAD,
    }
    values.update(overrides)
    return ArchitectureInvariantEvidence(**values)


def review(**overrides: object) -> ReviewEvidence:
    values = {
        "independent_review": True,
        "adversarial_review": True,
        "protected_review_required": True,
        "bound_head_sha": HEAD,
    }
    values.update(overrides)
    return ReviewEvidence(**values)


def test_green_task_can_reach_production_ready_with_lightweight_evidence() -> None:
    decision = evaluate_task_quality(
        contract=contract(),
        evidence=TaskQualityEvidence(
            tests=make_test_evidence(),
            dependencies=dependencies(),
        ),
    )

    assert decision.state is ReadinessState.PRODUCTION_READY
    assert decision.allowed
    assert decision.reason_codes == ()


def test_yellow_protected_task_cannot_reach_architecture_green_without_required_evidence() -> None:
    decision = evaluate_task_quality(
        contract=contract(declared_risk="yellow"),
        evidence=TaskQualityEvidence(
            tests=make_test_evidence(),
            dependencies=dependencies(),
        ),
    )

    assert decision.state is ReadinessState.TESTS_GREEN
    assert "ARCHITECTURE_GREEN_REQUIRED" in decision.reason_codes
    assert "INDEPENDENT_REVIEW_EVIDENCE_REQUIRED" in decision.reason_codes


def test_yellow_task_reaches_production_ready_with_architecture_and_review_evidence() -> None:
    decision = evaluate_task_quality(
        contract=contract(declared_risk="yellow"),
        evidence=TaskQualityEvidence(
            tests=make_test_evidence(),
            dependencies=dependencies(),
            architecture=architecture(),
            review=review(),
        ),
    )

    assert decision.state is ReadinessState.PRODUCTION_READY
    assert decision.reason_codes == ()


def test_architecture_green_is_distinct_from_production_ready() -> None:
    decision = evaluate_task_quality(
        contract=contract(declared_risk="yellow"),
        evidence=TaskQualityEvidence(
            dependencies=dependencies(),
            architecture=architecture(),
            review=review(),
        ),
    )

    assert decision.state is ReadinessState.ARCHITECTURE_GREEN
    assert not decision.allowed
    assert "TEST_EVIDENCE_REQUIRED" in decision.reason_codes


def test_mock_only_evidence_blocks_production_contract_proof_requirement() -> None:
    decision = evaluate_task_quality(
        contract=contract(),
        evidence=TaskQualityEvidence(
            tests=QualityTestEvidence(
                tests_passed=True,
                diff_check_passed=True,
                compile_passed=True,
                strength=EvidenceStrength.MOCK_ONLY,
                production_contract_required=True,
                bound_head_sha=HEAD,
            ),
            dependencies=dependencies(),
        ),
    )

    assert decision.state is ReadinessState.BLOCKED
    assert "MOCK_ONLY_EVIDENCE_INSUFFICIENT" in decision.reason_codes


def test_exact_head_sha_change_invalidates_prior_review_and_evidence() -> None:
    decision = evaluate_task_quality(
        contract=contract(declared_risk="yellow"),
        evidence=TaskQualityEvidence(
            tests=make_test_evidence(),
            dependencies=dependencies(),
            architecture=architecture(bound_head_sha="3" * 40),
            review=review(bound_head_sha="3" * 40),
        ),
    )

    assert "ARCHITECTURE_EVIDENCE_SHA_MISMATCH" in decision.reason_codes
    assert "REVIEW_SHA_MISMATCH" in decision.reason_codes


def test_tests_green_alone_never_implies_architecture_or_production_ready() -> None:
    decision = evaluate_task_quality(
        contract=contract(declared_risk="yellow"),
        evidence=TaskQualityEvidence(
            tests=make_test_evidence(),
            dependencies=dependencies(),
        ),
    )

    assert decision.state is ReadinessState.TESTS_GREEN
    assert decision.state is not ReadinessState.ARCHITECTURE_GREEN
    assert not decision.allowed


def test_runtime_proven_requires_post_merge_canary_evidence() -> None:
    pre_merge = evaluate_task_quality(
        contract=contract(),
        evidence=TaskQualityEvidence(
            tests=make_test_evidence(),
            dependencies=dependencies(),
            runtime=RuntimeEvidence(RuntimeEvidenceState.PRE_MERGE_ONLY, bound_head_sha=HEAD),
        ),
    )
    post_merge = evaluate_task_quality(
        contract=contract(),
        evidence=TaskQualityEvidence(
            tests=make_test_evidence(),
            dependencies=dependencies(),
            runtime=RuntimeEvidence(RuntimeEvidenceState.POST_MERGE_CANARY_GREEN, bound_head_sha=HEAD),
        ),
    )

    assert pre_merge.state is ReadinessState.PRODUCTION_READY
    assert post_merge.state is ReadinessState.RUNTIME_PROVEN


def test_missing_evidence_fails_closed_with_stable_reason_code() -> None:
    decision = evaluate_task_quality(contract=contract(), evidence=None)

    assert decision.state is ReadinessState.BLOCKED
    assert "MISSING_EVIDENCE_BUNDLE" in decision.reason_codes


def test_malformed_task_contract_fails_closed_during_normalization() -> None:
    with pytest.raises(ValueError, match="MISSING_TASK_CONTRACT_FIELD"):
        normalize_task_contract({"schema": "skeleton.runner_task.v1"})


def test_receipts_are_public_safe_metadata_only() -> None:
    decision = evaluate_task_quality(
        contract=contract(allowed_files=("secret/internal-name.py",)),
        evidence=TaskQualityEvidence(tests=make_test_evidence(), dependencies=dependencies()),
    )

    receipt_text = str(decision.public_receipt)
    assert "secret/internal-name.py" not in receipt_text
    assert decision.public_receipt["allowed_file_count"] == 1
    assert "allowed_file_set_hash" in decision.public_receipt
