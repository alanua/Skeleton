from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path

import pytest

from core.runner_task import (
    MAX_VALIDATION_TIMEOUT_SECONDS,
    PRIVACY_BOUNDARIES,
    REQUESTED_CAPABILITIES,
    RUNNER_TASK_SCHEMA,
    TASK_KINDS,
    RunnerTask,
    RunnerTaskValidationError,
)


ROOT = Path(__file__).resolve().parents[1]


def valid_task() -> dict[str, object]:
    return {
        "schema": RUNNER_TASK_SCHEMA,
        "repo": "alanua/Skeleton",
        "branch": "runner/issue-1510",
        "base_sha": "f" * 40,
        "task_kind": "code_edit",
        "payload": {"issue_number": 1510, "options": {"dry_run": True}},
        "requested_capabilities": [
            "test_execution",
            "repository_read",
            "repository_write_allowlisted",
        ],
        "allowed_files": [
            "tests/test_runner_task.py",
            "core/runner_task.py",
            "schemas/runner_task.schema.json",
        ],
        "forbidden_actions": ["no deployment", "no scope expansion"],
        "validation_commands": [["python3", "-m", "pytest", "-q"]],
        "validation_timeout_seconds": 900,
        "expected_output": ["draft PR", "test results"],
        "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
        "approval_reference": "operator-chat-2026-07-05-slice1-1496",
        "idempotency_key": "skeleton-runner-task-envelope-slice-1-v1",
    }


def reason(mapping: dict[str, object]) -> str:
    with pytest.raises(RunnerTaskValidationError) as excinfo:
        RunnerTask.from_mapping(mapping)
    return excinfo.value.reason_code


def test_round_trip_normalization_and_immutability() -> None:
    source = valid_task()
    task = RunnerTask.from_mapping(source)
    assert RunnerTask.from_json(task.to_json()).to_mapping() == task.to_mapping()
    assert task.to_mapping()["requested_capabilities"] == sorted(
        source["requested_capabilities"]
    )
    assert task.to_mapping()["allowed_files"] == sorted(source["allowed_files"])
    with pytest.raises(FrozenInstanceError):
        task.repo = "other/repo"  # type: ignore[misc]
    with pytest.raises(TypeError):
        task.payload["new"] = True  # type: ignore[index]


def test_deterministic_json_for_set_like_fields() -> None:
    first = valid_task()
    second = valid_task()
    for field in (
        "requested_capabilities",
        "allowed_files",
        "forbidden_actions",
        "expected_output",
    ):
        value = second[field]
        assert isinstance(value, list)
        value.reverse()
    second["payload"] = {"options": {"dry_run": True}, "issue_number": 1510}
    assert RunnerTask.from_mapping(first).to_json() == RunnerTask.from_mapping(
        second
    ).to_json()


def test_unknown_and_missing_fields_fail_closed() -> None:
    mapping = valid_task()
    mapping["model"] = "vendor"
    assert reason(mapping) == "UNKNOWN_TASK_FIELD"
    mapping = valid_task()
    mapping.pop("approval_reference")
    assert reason(mapping) == "MISSING_TASK_FIELD"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("schema", "skeleton.runner_task.v2", "INVALID_TASK_SCHEMA"),
        ("repo", "Skeleton", "INVALID_REPOSITORY"),
        ("branch", "segment..name", "INVALID_BRANCH"),
        ("branch", "release.lock", "INVALID_BRANCH"),
        ("base_sha", "abc123", "INVALID_BASE_SHA"),
        ("task_kind", "vendor_model", "INVALID_TASK_KIND"),
        ("privacy_boundary", "INVALID_BOUNDARY", "INVALID_PRIVACY_BOUNDARY"),
        ("approval_reference", "", "INVALID_APPROVAL_REFERENCE"),
        ("idempotency_key", "contains spaces", "INVALID_IDEMPOTENCY_KEY"),
    ],
)
def test_scalar_fields_fail_closed(field: str, value: object, expected: str) -> None:
    mapping = valid_task()
    mapping[field] = value
    assert reason(mapping) == expected


def test_capabilities_and_files_are_unique_and_allowlisted() -> None:
    mapping = valid_task()
    mapping["requested_capabilities"] = ["repository_read", "unknown_capability"]
    assert reason(mapping) == "INVALID_REQUESTED_CAPABILITY"
    mapping = valid_task()
    mapping["requested_capabilities"] = ["repository_read", "repository_read"]
    assert reason(mapping) == "DUPLICATE_REQUESTED_CAPABILITIES"
    mapping = valid_task()
    mapping["allowed_files"] = ["core/runner_task.py", "core/runner_task.py"]
    assert reason(mapping) == "DUPLICATE_ALLOWED_FILES"


@pytest.mark.parametrize(
    "path",
    ["segment/../file.py", "segment//file.py", "segment/", "."],
)
def test_allowed_files_reject_unsafe_paths(path: str) -> None:
    mapping = valid_task()
    mapping["allowed_files"] = [path]
    assert reason(mapping) == "INVALID_ALLOWED_FILE"


@pytest.mark.parametrize(
    "timeout", [0, MAX_VALIDATION_TIMEOUT_SECONDS + 1, True, "900"]
)
def test_timeout_is_bounded(timeout: object) -> None:
    mapping = valid_task()
    mapping["validation_timeout_seconds"] = timeout
    assert reason(mapping) == "INVALID_VALIDATION_TIMEOUT"


def test_validation_commands_use_argv_arrays() -> None:
    mapping = valid_task()
    mapping["validation_commands"] = ["single command string"]
    assert reason(mapping) == "INVALID_VALIDATION_COMMAND"


def test_payload_is_bounded_json() -> None:
    mapping = valid_task()
    mapping["payload"] = {
        "none": None,
        "boolean": False,
        "integer": 2,
        "float": 1.5,
        "array": ["alpha", "beta"],
    }
    assert RunnerTask.from_mapping(mapping).to_mapping()["payload"]["array"] == [
        "alpha",
        "beta",
    ]
    mapping = valid_task()
    mapping["payload"] = {"value": math.nan}
    assert reason(mapping) == "INVALID_ROUTE_PAYLOAD"


def test_schema_matches_python_contract() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "runner_task.schema.json").read_text(encoding="utf-8")
    )
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(valid_task())
    assert schema["properties"]["schema"]["const"] == RUNNER_TASK_SCHEMA
    assert set(schema["properties"]["task_kind"]["enum"]) == TASK_KINDS
    assert set(
        schema["properties"]["requested_capabilities"]["items"]["enum"]
    ) == REQUESTED_CAPABILITIES
    assert set(schema["properties"]["privacy_boundary"]["enum"]) == PRIVACY_BOUNDARIES
