from __future__ import annotations

from core.architecture_invariants import (
    ArchitectureEvidence,
    EvidenceBinding,
    EvidenceKind,
    evaluate_architecture_invariants,
)
from core.quality_evidence import (
    EvidenceStatus,
    ProbeStatus,
    ProductionContractEvidence,
    TestEvidence as QualityTestEvidence,
    ReadinessConfig,
    ReadinessState,
    RuntimeEvidence,
    evaluate_readiness,
)
from core.task_quality_gate import TASK_SPEC_SCHEMA, validate_task_spec


BASE_SHA = "47320dab7740b6c26d006e1b6e3e8d23cd7bcca5"
HEAD_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MERGED_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
REPO = "alanua/Skeleton"


def task_validation():
    return validate_task_spec(
        {
            "schema": TASK_SPEC_SCHEMA,
            "repository": REPO,
            "base": {"ref": "main", "sha": BASE_SHA},
            "branch": "runner/issue-3156",
            "task_kind": "code_generation",
            "risk": "medium",
            "protected_intent": "none",
            "requested_capabilities": [
                "repository_read",
                "repository_write",
                "test_execution",
            ],
            "allowed_files": ["core/quality_evidence.py"],
            "forbidden_actions": ["no runtime mutation"],
            "validation_requirements": {
                "commands": [["python3", "-m", "pytest", "-q"]],
                "requires_architecture_evidence": True,
                "requires_real_production_contract": True,
            },
            "expected_output": ["pure Phase 1 QA foundation only"],
            "privacy_boundary": "PUBLIC_SAFE_POLICY_METADATA_ONLY",
            "dependencies": ["issue-3151"],
            "evidence_expectations": {
                "tests": True,
                "architecture": True,
                "production_contract": True,
                "runtime": False,
            },
        }
    )


def binding(head_sha: str = HEAD_SHA) -> EvidenceBinding:
    return EvidenceBinding(repository=REPO, base_sha=BASE_SHA, head_sha=head_sha)


def green_test_evidence() -> QualityTestEvidence:
    return QualityTestEvidence(
        binding=binding(),
        status=EvidenceStatus.PASS,
        command_count=3,
    )


def architecture_green():
    return evaluate_architecture_invariants(
        required=True,
        evidence=ArchitectureEvidence(
            binding=binding(),
            kind=EvidenceKind.STATIC_REVIEW,
            invariant_ids=("phase1-pure",),
            passed=True,
            reviewer_id="public-review-3156",
        ),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )


def production_contract(kind: EvidenceKind = EvidenceKind.SANDBOX):
    return ProductionContractEvidence(
        binding=binding(),
        status=EvidenceStatus.PASS,
        kind=kind,
        contract_id="sandbox-production-contract-3156",
    )


def runtime_evidence(
    *,
    merged_main_sha: str = MERGED_SHA,
    runtime_sha: str | None = MERGED_SHA,
) -> RuntimeEvidence:
    return RuntimeEvidence(
        repository=REPO,
        base_sha=BASE_SHA,
        reviewed_head_sha=HEAD_SHA,
        merged_main_sha=merged_main_sha,
        runtime_sha=runtime_sha,
        immutable_runtime_revision=None,
        canary_status=ProbeStatus.SUCCESS,
        probe_status=ProbeStatus.SUCCESS,
        evidence_id="runtime-evidence-3156",
    )


def config(
    *,
    architecture_required: bool = True,
    real_production_contract_required: bool = True,
) -> ReadinessConfig:
    return ReadinessConfig(
        architecture_required=architecture_required,
        real_production_contract_required=real_production_contract_required,
    )


def test_tests_green_does_not_imply_architecture_green_or_production_ready() -> None:
    result = evaluate_readiness(
        task_validation=task_validation(),
        test_evidence=green_test_evidence(),
        architecture_result=None,
        production_contract_evidence=production_contract(),
        runtime_evidence=None,
        config=config(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )

    assert result.state is ReadinessState.TESTS_GREEN
    assert "MISSING_ARCHITECTURE_RESULT" in result.reason_codes


def test_mock_only_evidence_cannot_satisfy_real_production_contract() -> None:
    result = evaluate_readiness(
        task_validation=task_validation(),
        test_evidence=green_test_evidence(),
        architecture_result=architecture_green(),
        production_contract_evidence=production_contract(EvidenceKind.MOCK),
        runtime_evidence=None,
        config=config(real_production_contract_required=True),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )

    assert result.state is ReadinessState.ARCHITECTURE_GREEN
    assert "MOCK_ONLY_PRODUCTION_CONTRACT_EVIDENCE" in result.reason_codes


def test_sandbox_production_contract_can_contribute_to_production_ready_pre_merge() -> None:
    result = evaluate_readiness(
        task_validation=task_validation(),
        test_evidence=green_test_evidence(),
        architecture_result=architecture_green(),
        production_contract_evidence=production_contract(EvidenceKind.SANDBOX),
        runtime_evidence=runtime_evidence(),
        config=config(real_production_contract_required=True),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        merged=False,
    )

    assert result.state is ReadinessState.PRODUCTION_READY
    assert result.reason_codes == ()


def test_production_ready_is_maximum_pre_merge_state() -> None:
    result = evaluate_readiness(
        task_validation=task_validation(),
        test_evidence=green_test_evidence(),
        architecture_result=architecture_green(),
        production_contract_evidence=production_contract(),
        runtime_evidence=runtime_evidence(),
        config=config(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        merged=False,
    )

    assert result.state is ReadinessState.PRODUCTION_READY


def test_regression_3154_enum_only_post_merge_canary_cannot_runtime_prove() -> None:
    result = evaluate_readiness(
        task_validation=task_validation(),
        test_evidence=green_test_evidence(),
        architecture_result=architecture_green(),
        production_contract_evidence=production_contract(),
        runtime_evidence="POST_MERGE_CANARY_GREEN",
        config=config(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        merged=True,
    )

    assert result.state is ReadinessState.PRODUCTION_READY
    assert "STRUCTURED_RUNTIME_EVIDENCE_REQUIRED" in result.reason_codes


def test_runtime_proven_requires_merge_bound_runtime_identity_and_probe_success() -> None:
    result = evaluate_readiness(
        task_validation=task_validation(),
        test_evidence=green_test_evidence(),
        architecture_result=architecture_green(),
        production_contract_evidence=production_contract(),
        runtime_evidence=runtime_evidence(),
        config=config(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        merged=True,
    )

    assert result.state is ReadinessState.RUNTIME_PROVEN
    assert result.reason_codes == ()


def test_mismatched_runtime_identity_fails_closed() -> None:
    result = evaluate_readiness(
        task_validation=task_validation(),
        test_evidence=green_test_evidence(),
        architecture_result=architecture_green(),
        production_contract_evidence=production_contract(),
        runtime_evidence=runtime_evidence(runtime_sha=HEAD_SHA),
        config=config(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        merged=True,
    )

    assert result.state is ReadinessState.PRODUCTION_READY
    assert "RUNTIME_SHA_MERGE_MISMATCH" in result.reason_codes


def test_readiness_cannot_skip_configured_prerequisites_even_with_runtime_evidence() -> None:
    result = evaluate_readiness(
        task_validation=task_validation(),
        test_evidence=None,
        architecture_result=architecture_green(),
        production_contract_evidence=production_contract(),
        runtime_evidence=runtime_evidence(),
        config=config(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        merged=True,
    )

    assert result.state is ReadinessState.TASK_SPEC_ACCEPTED
    assert "MISSING_TEST_EVIDENCE" in result.reason_codes


def test_head_movement_invalidates_head_bound_readiness_evidence() -> None:
    result = evaluate_readiness(
        task_validation=task_validation(),
        test_evidence=green_test_evidence(),
        architecture_result=architecture_green(),
        production_contract_evidence=production_contract(),
        runtime_evidence=runtime_evidence(),
        config=config(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
        current_head_sha="cccccccccccccccccccccccccccccccccccccccc",
        merged=True,
    )

    assert result.state is ReadinessState.NOT_READY
    assert "HEAD_MOVED" in result.reason_codes


def test_readiness_receipt_contains_public_safe_metadata_only() -> None:
    result = evaluate_readiness(
        task_validation=task_validation(),
        test_evidence=green_test_evidence(),
        architecture_result=architecture_green(),
        production_contract_evidence=production_contract(),
        runtime_evidence=None,
        config=config(),
        repository=REPO,
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )

    assert result.receipt["state"] == "PRODUCTION_READY"
    assert result.receipt["runtime_proven"] is False
    assert "allowed_files" not in result.receipt
    assert "contract_id" not in result.receipt
