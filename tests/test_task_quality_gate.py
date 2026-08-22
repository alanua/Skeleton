from __future__ import annotations

import pytest

from core.quality_evidence import EvidenceLevel, HeadBoundEvidence
from core.task_quality_gate import (
    AllowedScopeKind,
    DeclaredScopeProtection,
    GateStatus,
    Phase1GateConfig,
    PrivacyBoundaryClass,
    TaskSpec,
    TaskSpecValidationError,
    evaluate_phase1_quality,
)


BASE = "a" * 40
HEAD = "b" * 40


def current_runner_task() -> dict[str, object]:
    return {
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "branch": "runner/production-qa-phase1-current-runner-compat-v1",
        "base_sha": BASE,
        "task_kind": "code_generation",
        "payload": {
            "operation": "repair_phase1_taskspec_current_runner_compatibility"
        },
        "requested_capabilities": [
            "repository_read",
            "repository_write",
            "test_execution",
        ],
        "allowed_files": [
            "core/task_quality_gate.py",
            "core/quality_evidence.py",
            "tests/test_task_quality_gate.py",
            "tests/test_quality_evidence.py",
            "docs/PRODUCTION_ENGINEERING_QUALITY_PIPELINE.md",
        ],
        "forbidden_actions": ["no merge"],
        "validation": ["python3 -m pytest -q"],
        "expected_output": ["NO MERGE"],
        "privacy_boundary": "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "idempotency_key": "production-qa-phase1-current-runner-compat-47320dab-20260822-v1",
    }


def evidence() -> HeadBoundEvidence:
    return HeadBoundEvidence.from_mapping(
        {
            "repo": "alanua/Skeleton",
            "base_sha": BASE,
            "head_sha": HEAD,
            "validation_commands": [["python3", "-m", "pytest", "-q"]],
            "tests_passed": True,
        }
    )


def reason(mapping: dict[str, object]) -> str:
    with pytest.raises(TaskSpecValidationError) as excinfo:
        TaskSpec.normalize(mapping)
    return excinfo.value.reason_code


def test_current_runner_shape_normalizes_without_changing_repo_base_head() -> None:
    task = TaskSpec.normalize(current_runner_task())

    assert task.repo == "alanua/Skeleton"
    assert task.base_ref == BASE
    assert task.head_ref == "runner/production-qa-phase1-current-runner-compat-v1"
    assert task.requested_capabilities == (
        "repository_read",
        "repository_write",
        "test_execution",
    )
    assert task.privacy_boundary.boundary_class is PrivacyBoundaryClass.PUBLIC_SAFE


def test_current_privacy_boundary_composite_is_protected_and_public_safe_hash_only() -> None:
    mapping = current_runner_task()
    mapping[
        "privacy_boundary"
    ] = "PRIVATE_PRIVILEGE_STATE_LOCAL_ONLY / PUBLIC_SAFE_HASH_STATUS_ONLY"

    task = TaskSpec.normalize(mapping)
    public = task.public_normalized_mapping()

    assert task.privacy_boundary.boundary_class is PrivacyBoundaryClass.PROTECTED_PRIVATE
    assert task.declared_scope_protection is DeclaredScopeProtection.PROTECTED_REVIEW_REQUIRED
    assert public["privacy_boundary"] == {
        "boundary_class": "PROTECTED_PRIVATE",
        "public_policy_tokens": ["PUBLIC_SAFE_HASH_STATUS_ONLY"],
    }
    assert "PRIVATE_PRIVILEGE_STATE_LOCAL_ONLY" not in repr(public)


def test_current_allowed_scope_globs_are_declared_scope_only() -> None:
    mapping = current_runner_task()
    mapping["allowed_files"] = ["home_edge/generative_visuals/**", "tests/**"]

    task = TaskSpec.normalize(mapping)

    assert [scope.kind for scope in task.allowed_scopes] == [
        AllowedScopeKind.REPOSITORY_GLOB,
        AllowedScopeKind.REPOSITORY_GLOB,
    ]
    assert {scope.evidence_level for scope in task.allowed_scopes} == {
        EvidenceLevel.DECLARED_ONLY
    }


def test_current_idempotency_key_with_repository_identity_is_accepted() -> None:
    mapping = current_runner_task()
    mapping[
        "idempotency_key"
    ] = "validate-pr-branch:alanua/Skeleton:runner/issue-3176:47320dab"

    task = TaskSpec.normalize(mapping)

    assert task.idempotency_key == mapping["idempotency_key"]


@pytest.mark.parametrize(
    "allowed_file",
    [
        "/tmp/private",
        "../secrets",
        "tests/../core/task_quality_gate.py",
        "",
        "*",
        "**",
        "*/**",
        "tests/\nsecret.py",
    ],
)
def test_allowed_scopes_reject_escape_or_unbounded_patterns(allowed_file: str) -> None:
    mapping = current_runner_task()
    mapping["allowed_files"] = [allowed_file]

    assert reason(mapping) == "INVALID_ALLOWED_FILE_SCOPE"


@pytest.mark.parametrize(
    "idempotency_key",
    [
        "contains spaces",
        "validate:../escape",
        "validate:/absolute",
        "validate:\ncontrol",
        "validate:alanua//Skeleton",
    ],
)
def test_idempotency_key_rejects_path_or_control_semantics(idempotency_key: str) -> None:
    mapping = current_runner_task()
    mapping["idempotency_key"] = idempotency_key

    assert reason(mapping) == "INVALID_IDEMPOTENCY_KEY"


def test_protected_declared_scope_remains_protected_review_required() -> None:
    mapping = current_runner_task()
    mapping["privacy_boundary"] = ["LOCAL_PRIVATE", "PUBLIC_SAFE_POLICY_METADATA_ONLY"]
    task = TaskSpec.normalize(mapping)
    result = evaluate_phase1_quality(task=task, evidence=evidence(), head_sha=HEAD)

    assert task.declared_scope_protection is DeclaredScopeProtection.PROTECTED_REVIEW_REQUIRED
    assert result.protected_review_required
    assert result.status is GateStatus.REVIEW_REQUIRED


@pytest.mark.parametrize(
    "caller_value",
    [True, "ARCHITECTURE_GREEN", ["ARCHITECTURE_GREEN"]],
)
def test_architecture_required_cannot_be_satisfied_by_caller_values(
    caller_value: object,
) -> None:
    task = TaskSpec.normalize(current_runner_task())
    result = evaluate_phase1_quality(
        task=task,
        evidence=evidence(),
        head_sha=HEAD,
        config=Phase1GateConfig(architecture_required=True),
        architecture_attestation=caller_value,
    )

    assert result.status is GateStatus.REVIEW_REQUIRED
    assert result.evidence_level is EvidenceLevel.ARCHITECTURE_REVIEW_REQUIRED
    assert "ARCHITECTURE_REVIEW_REQUIRED" in result.reasons


def test_optional_architecture_and_production_contract_gates_remain_optional() -> None:
    task = TaskSpec.normalize(current_runner_task())

    result = evaluate_phase1_quality(
        task=task,
        evidence=evidence(),
        head_sha=HEAD,
        config=Phase1GateConfig(
            architecture_required=False,
            production_contract_required=False,
        ),
        architecture_attestation=True,
        production_contract_attestation=["caller claim"],
    )

    assert result.status is GateStatus.FAIL
    assert "CALLER_ARCHITECTURE_ATTESTATION_IGNORED" in result.reasons
    assert "CALLER_PRODUCTION_CONTRACT_ATTESTATION_IGNORED" in result.reasons
    assert result.evidence_level is EvidenceLevel.HEAD_BOUND_VALIDATION


def test_phase1_quality_never_returns_runtime_proven() -> None:
    task = TaskSpec.normalize(current_runner_task())

    result = evaluate_phase1_quality(task=task, evidence=evidence(), head_sha=HEAD)

    assert result.status is GateStatus.PASS
    assert result.evidence_level is EvidenceLevel.HEAD_BOUND_VALIDATION
    assert result.evidence_level is not EvidenceLevel.RUNTIME_PROVEN
    assert result.evidence_level is not EvidenceLevel.ARCHITECTURE_GREEN


def test_head_movement_invalidates_head_bound_quality_evidence() -> None:
    task = TaskSpec.normalize(current_runner_task())

    result = evaluate_phase1_quality(task=task, evidence=evidence(), head_sha="c" * 40)

    assert result.status is GateStatus.FAIL
    assert result.evidence_level is EvidenceLevel.DECLARED_ONLY
    assert "HEAD_BOUND_EVIDENCE_INVALIDATED" in result.reasons
