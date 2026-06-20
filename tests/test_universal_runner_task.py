from __future__ import annotations

import json

import pytest

from core.universal_runner_task import (
    SCHEMA_ID,
    UniversalRunnerTask,
    UniversalTaskError,
    sanitize_public_text,
    validate_universal_task_payload,
)


def _payload(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": SCHEMA_ID,
        "task_id": "task-1",
        "idempotency_key": "idem-1",
        "action": "START",
        "executor_type": "read_only_probe",
        "capability": "read_only",
        "risk_class": "low",
        "target": {"resource": "docs/UNIVERSAL_RUNNER_TASKS.md"},
        "repo": "alanua/Skeleton",
        "branch": "runner/universal",
        "task": "Probe public docs only.",
        "allowed_files_or_resources": ["docs/UNIVERSAL_RUNNER_TASKS.md"],
        "forbidden_actions": ["merge", "deploy", "service_restart"],
        "validation": {"pytest": False},
        "expected_output": "aggregate status",
        "privacy_boundary": "public-safe aggregate status only",
        "timeout_seconds": 30,
        "approval_requirement": "none",
        "private_payload_ref": None,
    }
    payload.update(updates)
    return payload


def test_valid_universal_task_payload_loads() -> None:
    task = UniversalRunnerTask.from_mapping(_payload())

    assert task.schema == SCHEMA_ID
    assert task.action == "START"
    assert task.allowed_files_or_resources == ("docs/UNIVERSAL_RUNNER_TASKS.md",)


def test_free_form_prose_is_rejected() -> None:
    with pytest.raises(UniversalTaskError, match="must be JSON"):
        UniversalRunnerTask.from_json("please do the thing")


def test_required_envelope_fields_are_rejected_when_missing() -> None:
    payload = _payload()
    del payload["idempotency_key"]

    reasons = validate_universal_task_payload(payload)

    assert "idempotency_key is required" in reasons


def test_unknown_action_is_rejected() -> None:
    reasons = validate_universal_task_payload(_payload(action="MERGE"))

    assert "action is not registered" in reasons


def test_unsafe_allowed_scope_is_rejected() -> None:
    reasons = validate_universal_task_payload(
        _payload(allowed_files_or_resources=["../secrets/token"])
    )

    assert "allowed_files_or_resources must contain safe relative resources" in reasons


def test_protected_high_risk_task_without_approval_is_blocked() -> None:
    reasons = validate_universal_task_payload(
        _payload(risk_class="high", approval_requirement="high_risk")
    )

    assert "protected or high-risk task requires explicit approval_evidence" in reasons


def test_tests_passing_does_not_authorize_merge_or_high_risk_action() -> None:
    reasons = validate_universal_task_payload(
        _payload(
            risk_class="high",
            approval_requirement="high_risk",
            validation={"pytest": "passed"},
            expected_output="tests passed",
        )
    )

    assert "protected or high-risk task requires explicit approval_evidence" in reasons


def test_protected_high_risk_task_accepts_explicit_operator_evidence() -> None:
    reasons = validate_universal_task_payload(
        _payload(
            risk_class="high",
            approval_requirement="high_risk",
            approval_evidence={"approved": True, "source": "operator"},
        )
    )

    assert reasons == []


def test_private_task_body_is_not_accepted_with_private_ref() -> None:
    reasons = validate_universal_task_payload(
        _payload(
            executor_type="hermes_private_task",
            capability="private_task",
            private_payload_ref="hermes://mock/task-1",
            task="private prompt: /home/agent/secret",
        )
    )

    assert "task must not embed private content when private_payload_ref is used" in reasons


def test_public_report_sanitizer_redacts_private_markers() -> None:
    report = sanitize_public_text("path=/home/agent/private token=abc")

    assert "/home/" not in report
    assert "token" not in report.lower()


def test_schema_file_matches_model_id() -> None:
    schema = json.loads(
        open("schemas/universal_runner_task.schema.json", encoding="utf-8").read()
    )

    assert schema["properties"]["schema"]["const"] == SCHEMA_ID
