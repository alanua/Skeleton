from __future__ import annotations

import pytest

from core.quality_evidence import (
    ARCHITECTURE_EVALUATOR_REQUIRED,
    POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1,
    PRODUCTION_CONTRACT_EVIDENCE_REQUIRED,
    PRODUCTION_READY,
    TASK_SPEC_VALIDATED,
    TESTS_GREEN,
    HeadBoundTestEvidence,
    MockProductionContractEvidence,
    Phase1EvidenceBundle,
    ProductionContractPreMergePlaceholder,
)
from core.task_quality_gate import (
    PROTECTED_SCOPE_CLASSIFICATION,
    TASK_SPEC_SCHEMA,
    Phase1ReadinessConfig,
    TaskSpec,
    TaskSpecValidationError,
    evaluate_phase1_readiness,
)


BASE = "47320dab7740b6c26d006e1b6e3e8d23cd7bcca5"
HEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def valid_spec(**overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "schema": TASK_SPEC_SCHEMA,
        "repo": "alanua/Skeleton",
        "base_sha": BASE,
        "branch": "runner/production-qa-phase1-final-task-spec-v1",
        "task_kind": "code_generation",
        "risk": "low",
        "protected_intent": False,
        "requested_capabilities": [
            "repository_write",
            "test_execution",
            "repository_read",
        ],
        "allowed_files": [
            "tests/test_task_quality_gate.py",
            "core/task_quality_gate.py",
            "core/quality_evidence.py",
            "tests/test_quality_evidence.py",
            "docs/PRODUCTION_ENGINEERING_QUALITY_PIPELINE.md",
        ],
        "forbidden_actions": ["no merge", "no runtime materialization"],
        "validation_requirements": [
            "python3 -m pytest -q",
            "python3 -m py_compile touched Python",
            "git diff --check",
        ],
        "expected_output": [
            "Phase 1 TaskSpec/readiness foundation only",
            "next_action=SEMANTIC_EXACT_HEAD_REVIEW_THEN_PHASE1B_3150",
        ],
        "privacy_boundary": "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "dependencies": ["#3150", "#3151", "#3153", "#3160"],
        "evidence_expectations": ["deterministic_tests"],
        "idempotency_key": "production-qa-phase1-final-task-spec-47320dab-20260822-v1",
    }
    spec.update(overrides)
    return spec


def green_tests(*, head_sha: str = HEAD, base_sha: str = BASE) -> HeadBoundTestEvidence:
    return HeadBoundTestEvidence.from_mapping(
        {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "passed": True,
            "total_tests": 4,
            "failed_tests": 0,
            "commands": ["python3 -m pytest -q"],
            "evidence_hash": "a" * 64,
        }
    )


def config(**overrides: object) -> Phase1ReadinessConfig:
    values = {"current_head_sha": HEAD}
    values.update(overrides)
    return Phase1ReadinessConfig(**values)


def spec_reason(mapping: dict[str, object]) -> str:
    with pytest.raises(TaskSpecValidationError) as excinfo:
        TaskSpec.from_mapping(mapping)
    return excinfo.value.reason_code


def test_task_spec_normalizes_claim_side_scope_and_capabilities() -> None:
    spec = TaskSpec.from_mapping(valid_spec())
    mapping = spec.to_mapping()

    assert mapping["allowed_files"] == sorted(valid_spec()["allowed_files"])
    assert mapping["requested_capabilities"] == sorted(
        valid_spec()["requested_capabilities"]
    )
    assert "touched_files" not in mapping
    assert "ObservedDiffImpact" not in mapping
    assert TaskSpec.from_mapping(mapping).to_json() == spec.to_json()


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("schema", "skeleton.runner_task.v1", "INVALID_SCHEMA"),
        ("repo", "Skeleton", "INVALID_REPOSITORY"),
        ("base_sha", "abc", "INVALID_BASE_SHA"),
        ("branch", "release.lock", "INVALID_BRANCH"),
        ("task_kind", "runtime_deploy", "INVALID_TASK_KIND"),
        ("risk", "green", "INVALID_RISK"),
        ("protected_intent", "false", "INVALID_PROTECTED_INTENT"),
        ("privacy_boundary", "PRIVATE_RUNTIME", "INVALID_PRIVACY_BOUNDARY"),
        ("idempotency_key", "contains spaces", "INVALID_IDEMPOTENCY_KEY"),
    ],
)
def test_scalar_malformed_spec_fields_fail_closed(
    field: str,
    value: object,
    expected: str,
) -> None:
    mapping = valid_spec()
    mapping[field] = value
    assert spec_reason(mapping) == expected


def test_incomplete_unknown_duplicate_and_contradictory_specs_fail_closed() -> None:
    mapping = valid_spec()
    mapping.pop("expected_output")
    assert spec_reason(mapping) == "MATERIAL_INCOMPLETE_TASK_SPEC"
    mapping = valid_spec()
    mapping["runtime"] = {"merged": True}
    assert spec_reason(mapping) == "UNKNOWN_TASK_SPEC_FIELD"
    mapping = valid_spec(requested_capabilities=["repository_read", "repository_read"])
    assert spec_reason(mapping) == "DUPLICATE_REQUESTED_CAPABILITIES"
    mapping = valid_spec(requested_capabilities=["repository_read", "test_execution"])
    assert spec_reason(mapping) == "CONTRADICTORY_TASK_SPEC"


def test_declared_allowed_files_rejects_unsafe_paths_and_is_not_touched_files() -> None:
    mapping = valid_spec(allowed_files=["core/task_quality_gate.py", "core/../x.py"])

    assert spec_reason(mapping) == "INVALID_DECLARED_ALLOWED_FILE"

    spec = TaskSpec.from_mapping(valid_spec())
    receipt = evaluate_phase1_readiness(
        spec,
        evidence=Phase1EvidenceBundle(test_evidence=green_tests()),
        config=config(),
    ).to_public_mapping()
    assert "touched_files" not in receipt
    assert receipt["declared_allowed_files_count"] == len(spec.allowed_files)


def test_architecture_required_cannot_be_satisfied_by_caller_claims() -> None:
    for architecture_claim in (
        {"passed": True},
        {"reviewer_id": "arch-reviewer"},
        {"invariant_ids": ["INV-1"]},
        "ARCHITECTURE_GREEN",
    ):
        receipt = evaluate_phase1_readiness(
            TaskSpec.from_mapping(valid_spec()),
            evidence=Phase1EvidenceBundle(
                test_evidence=green_tests(),
                architecture_evidence=architecture_claim,
            ),
            config=config(require_architecture_evidence=True),
        )

        assert receipt.state == TESTS_GREEN
        assert ARCHITECTURE_EVALUATOR_REQUIRED in receipt.reason_codes
        assert receipt.ready is False


def test_architecture_optional_absence_does_not_block_ready_state() -> None:
    receipt = evaluate_phase1_readiness(
        TaskSpec.from_mapping(valid_spec()),
        evidence=Phase1EvidenceBundle(test_evidence=green_tests()),
        config=config(require_architecture_evidence=False),
    )

    assert receipt.state == PRODUCTION_READY
    assert ARCHITECTURE_EVALUATOR_REQUIRED not in receipt.reason_codes


def test_optional_production_contract_absence_does_not_block_readiness() -> None:
    receipt = evaluate_phase1_readiness(
        TaskSpec.from_mapping(valid_spec()),
        evidence=Phase1EvidenceBundle(test_evidence=green_tests()),
        config=config(require_production_contract_proof=False),
    )

    assert receipt.state == PRODUCTION_READY
    assert PRODUCTION_CONTRACT_EVIDENCE_REQUIRED not in receipt.reason_codes


def test_required_production_contract_rejects_mock_and_placeholder_is_not_ready() -> None:
    spec = TaskSpec.from_mapping(valid_spec())

    mock_receipt = evaluate_phase1_readiness(
        spec,
        evidence=Phase1EvidenceBundle(
            test_evidence=green_tests(),
            production_contract_evidence=MockProductionContractEvidence(
                evidence_hash="b" * 64,
            ),
        ),
        config=config(require_production_contract_proof=True),
    )
    assert mock_receipt.state == TESTS_GREEN
    assert "MOCK_PRODUCTION_CONTRACT_EVIDENCE_REJECTED" in mock_receipt.reason_codes
    assert mock_receipt.ready is False

    placeholder_receipt = evaluate_phase1_readiness(
        spec,
        evidence=Phase1EvidenceBundle(
            test_evidence=green_tests(),
            production_contract_evidence=ProductionContractPreMergePlaceholder(
                evidence_hash="c" * 64,
            ),
        ),
        config=config(require_production_contract_proof=True),
    )
    assert placeholder_receipt.state == TESTS_GREEN
    assert "PRODUCTION_CONTRACT_AUTHENTICITY_PENDING_PHASE_3153" in (
        placeholder_receipt.reason_codes
    )
    assert placeholder_receipt.ready is False


def test_no_input_combination_can_produce_runtime_proven_in_phase1() -> None:
    for runtime_evidence in (
        {"merged": True, "head_sha": HEAD},
        {"status": "SUCCESS", "revision": "opaque"},
        "RUNTIME_PROVEN",
    ):
        receipt = evaluate_phase1_readiness(
            TaskSpec.from_mapping(valid_spec()),
            evidence=Phase1EvidenceBundle(
                test_evidence=green_tests(),
                runtime_evidence=runtime_evidence,
            ),
            config=config(),
        )

        assert receipt.state != "RUNTIME_PROVEN"
        assert receipt.runtime_proven is False
        assert POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1 in receipt.reason_codes


def test_exact_head_movement_invalidates_head_bound_test_evidence() -> None:
    receipt = evaluate_phase1_readiness(
        TaskSpec.from_mapping(valid_spec()),
        evidence=Phase1EvidenceBundle(
            test_evidence=green_tests(
                head_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            ),
        ),
        config=config(current_head_sha=HEAD),
    )

    assert receipt.state == TASK_SPEC_VALIDATED
    assert "HEAD_MOVED_INVALIDATES_EVIDENCE" in receipt.reason_codes
    assert receipt.tests_green is False


def test_protected_declared_scope_classifies_for_later_protected_gate() -> None:
    spec = TaskSpec.from_mapping(
        valid_spec(allowed_files=["core/task_quality_gate.py", "core/gate_engine.py"])
    )
    receipt = evaluate_phase1_readiness(
        spec,
        evidence=Phase1EvidenceBundle(test_evidence=green_tests()),
        config=config(),
    )

    assert spec.scope_classification == PROTECTED_SCOPE_CLASSIFICATION
    assert receipt.scope_classification == PROTECTED_SCOPE_CLASSIFICATION
    assert receipt.state == TESTS_GREEN
    assert "PROTECTED_POLICY_CHANGE_REQUIRED" in receipt.reason_codes
    assert receipt.ready is False


def test_regression_3167_arbitrary_structured_runtime_fields_do_not_raise_readiness() -> None:
    receipt = evaluate_phase1_readiness(
        TaskSpec.from_mapping(valid_spec()),
        evidence=Phase1EvidenceBundle(
            test_evidence=green_tests(),
            architecture_evidence={"passed": True},
            runtime_evidence={"merged": True, "valid_sha": HEAD, "status": "SUCCESS"},
        ),
        config=config(require_architecture_evidence=True),
    )

    assert receipt.state == TESTS_GREEN
    assert ARCHITECTURE_EVALUATOR_REQUIRED in receipt.reason_codes
    assert POSTMERGE_RUNTIME_PROOF_NOT_AVAILABLE_IN_PHASE1 in receipt.reason_codes
