from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
from unittest import mock

from core.runner_bridge import RunnerBridgeRequest, dry_run_runner_bridge


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "runner_bridge.schema.json"


def make_request(**overrides: object) -> RunnerBridgeRequest:
    values = {
        "repo": "alanua/Skeleton",
        "base_ref": "main",
        "task_title": "Add bridge dry-run",
        "task_body": "Task: add a deterministic dry-run bridge.",
        "allowed_files": ("core/runner_bridge.py", "tests/test_runner_bridge.py"),
        "protected_files": ("scripts/runner_poll_github_tasks.py",),
        "validation_commands": ("python3 -m pytest -q", "git diff --check"),
        "approval_marker": "APPROVED",
    }
    values.update(overrides)
    return RunnerBridgeRequest(**values)


def test_valid_request_renders_fenced_task_body() -> None:
    result = dry_run_runner_bridge(make_request())

    assert result.status == "dry_run"
    assert result.issue_number is None
    assert result.blocked_reason is None
    assert "Repository: alanua/Skeleton" in result.dry_run_summary
    assert "Base: main" in result.dry_run_summary
    assert "```task\nTask: add a deterministic dry-run bridge.\n```" in result.dry_run_summary


def test_missing_approval_blocks() -> None:
    result = dry_run_runner_bridge(make_request(approval_marker=""))

    assert result.status == "blocked"
    assert result.issue_number is None
    assert result.blocked_reason == (
        "approval_marker is required for runner_bridge dry-run handoff."
    )


def test_protected_file_overlap_blocks() -> None:
    result = dry_run_runner_bridge(
        make_request(protected_files=("core/runner_bridge.py",))
    )

    assert result.status == "blocked"
    assert result.blocked_reason == (
        "protected files overlap allowed_files: core/runner_bridge.py"
    )


def test_invalid_validation_command_blocks() -> None:
    result = dry_run_runner_bridge(
        make_request(validation_commands=("python3 -m pytest -q && gh issue list",))
    )

    assert result.status == "blocked"
    assert result.blocked_reason == (
        "invalid validation command: python3 -m pytest -q && gh issue list"
    )


def test_no_live_side_effects_are_needed() -> None:
    with mock.patch.object(subprocess, "run") as run, mock.patch.object(
        urllib.request, "urlopen"
    ) as urlopen:
        result = dry_run_runner_bridge(make_request())

    assert result.status == "dry_run"
    run.assert_not_called()
    urlopen.assert_not_called()


def test_runner_bridge_schema_file_exists_and_requires_contract_fields() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.runner_bridge.schema.json"
    assert set(schema["required"]) == {
        "repo",
        "base_ref",
        "task_title",
        "task_body",
        "allowed_files",
        "protected_files",
        "validation_commands",
        "approval_marker",
    }
