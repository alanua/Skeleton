from __future__ import annotations

from dataclasses import fields

from core.task_quality_gate import (
    TASK_SPEC_SCHEMA,
    TaskSpecStatus,
    public_receipt,
    validate_task_spec,
)


BASE_SHA = "47320dab7740b6c26d006e1b6e3e8d23cd7bcca5"


def spec(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": TASK_SPEC_SCHEMA,
        "repository": "alanua/Skeleton",
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
        "allowed_files": [
            "core/task_quality_gate.py",
            "tests/test_task_quality_gate.py",
        ],
        "forbidden_actions": [
            "no network",
            "no subprocess calls from evaluator code",
        ],
        "validation_requirements": {
            "commands": [["python3", "-m", "pytest", "-q"]],
            "requires_architecture_evidence": True,
            "requires_real_production_contract": False,
        },
        "expected_output": ["pure Phase 1 QA foundation only"],
        "privacy_boundary": "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "dependencies": ["issue-3151", "issue-3150"],
        "evidence_expectations": {
            "tests": True,
            "architecture": True,
            "production_contract": False,
            "runtime": False,
        },
    }
    value.update(overrides)
    return value


def test_valid_task_spec_produces_predicted_profile_only() -> None:
    validation = validate_task_spec(spec())

    assert validation.status is TaskSpecStatus.ACCEPTED
    assert validation.reason_codes == ()
    assert validation.predicted_profile is not None
    assert validation.predicted_profile.declared_scope.allowed_files == (
        "core/task_quality_gate.py",
        "tests/test_task_quality_gate.py",
    )
    assert not hasattr(validation.predicted_profile, "touched_files")
    assert not hasattr(validation.predicted_profile, "observed_impact")


def test_malformed_incomplete_task_spec_fails_closed_with_stable_reason_codes() -> None:
    validation = validate_task_spec({"schema": TASK_SPEC_SCHEMA})

    assert validation.status is TaskSpecStatus.REJECTED
    assert "MISSING_REPOSITORY" in validation.reason_codes
    assert "MISSING_ALLOWED_FILES" in validation.reason_codes
    assert validation.task_spec is None


def test_contradictory_architecture_expectation_fails_closed() -> None:
    invalid = spec(
        evidence_expectations={
            "tests": True,
            "architecture": False,
            "production_contract": False,
            "runtime": False,
        }
    )

    validation = validate_task_spec(invalid)

    assert validation.status is TaskSpecStatus.REJECTED
    assert "CONTRADICTORY_ARCHITECTURE_EXPECTATION" in validation.reason_codes


def test_self_modifying_future_policy_intent_is_classified_as_protected() -> None:
    invalid = spec(allowed_files=["INVARIANTS.yaml"], protected_intent="none")
    validation = validate_task_spec(invalid)
    assert "PROTECTED_POLICY_CHANGE_REQUIRES_INTENT" in validation.reason_codes

    valid = validate_task_spec(
        spec(
            allowed_files=["INVARIANTS.yaml"],
            protected_intent="protected-policy-change-required",
            risk="protected",
        )
    )
    assert valid.status is TaskSpecStatus.ACCEPTED
    assert valid.predicted_profile is not None
    assert (
        valid.predicted_profile.protected_intent
        == "protected-policy-change-required"
    )


def test_regression_3154_allowed_files_are_not_touched_files() -> None:
    validation = validate_task_spec(spec())

    assert validation.task_spec is not None
    task_fields = {field.name for field in fields(validation.task_spec)}
    assert "allowed_files" in task_fields
    assert "touched_files" not in task_fields
    assert "observed_impact" not in task_fields


def test_receipt_contains_public_safe_counts_not_file_lists() -> None:
    receipt = public_receipt(validate_task_spec(spec()))

    assert receipt["status"] == "ACCEPTED"
    assert receipt["declared_allowed_file_count"] == 2
    assert "allowed_files" not in receipt
    assert "touched_files" not in receipt
