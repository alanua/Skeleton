from __future__ import annotations

import pytest

from core.task_quality_gate import TaskSpecError, normalize_task_spec


def _task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {
        "schema": "skeleton.runner_task.v1",
        "repo": "alanua/Skeleton",
        "branch": "runner/production-qa-runner-integration-v3",
        "task_kind": "code_generation",
        "payload": {"operation": "integrate"},
        "requested_capabilities": [
            "repository_read",
            "repository_write",
            "test_execution",
        ],
        "allowed_files": ["core/review_gate.py", "tests/test_review_gate.py"],
        "forbidden_actions": ["no merge"],
        "validation": [
            "python3 -m pytest -q",
            "git diff --check",
        ],
        "expected_output": ["NO MERGE"],
        "privacy_boundary": "PUBLIC_SAFE_CONTROL_AND_EVIDENCE_METADATA_ONLY",
        "idempotency_key": "production-qa-phase4-runner-reviewer-integration-after-3177-v3",
    }
    task.update(overrides)
    return task


def test_current_runner_task_shape_normalizes_without_semantic_loss() -> None:
    spec = normalize_task_spec(_task())

    assert spec.repo == "alanua/Skeleton"
    assert spec.task_kind == "code_generation"
    assert spec.validation_commands == (
        ("python3", "-m", "pytest", "-q"),
        ("git", "diff", "--check"),
    )
    assert spec.idempotency_key.startswith("alanua/Skeleton:")
    assert spec.predicted_impact.level.value == "yellow"


def test_malformed_taskspec_rejected_before_codegen() -> None:
    with pytest.raises(TaskSpecError, match="INVALID_TASKSPEC_SCHEMA"):
        normalize_task_spec({**_task(), "schema": "skeleton.runner_task.v0"})


def test_unbounded_glob_scope_is_rejected() -> None:
    with pytest.raises(TaskSpecError, match="UNBOUNDED_TASKSPEC_ALLOWED_FILE"):
        normalize_task_spec({**_task(), "allowed_files": ["**/*.py"]})


def test_private_boundary_normalizes_but_predicts_red() -> None:
    spec = normalize_task_spec({**_task(), "privacy_boundary": "PRIVATE_LOCAL"})

    assert spec.privacy_boundary == "PRIVATE_LOCAL"
    assert spec.predicted_impact.level.value == "red"
