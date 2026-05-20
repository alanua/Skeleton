from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path
from unittest import mock

from core.runner_bridge import (
    ALLOWED_VALIDATION_COMMANDS,
    RunnerBridgeRequest,
    dry_run_runner_bridge,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "runner_bridge.schema.json"


def valid_request(**overrides: object) -> RunnerBridgeRequest:
    values = {
        "repo": "alanua/Skeleton",
        "base_ref": "main",
        "task_title": "Add bridge",
        "task_body": "Implement the task.\n\nReturn a concise report.",
        "allowed_files": ("core/runner_bridge.py", "tests/test_runner_bridge.py"),
        "protected_files": ("scripts/runner_poll_github_tasks.py",),
        "validation_commands": ("python3 -m pytest -q", "git diff --check"),
        "approval_marker": "operator-approved-stage-1-dry-run",
    }
    values.update(overrides)
    return RunnerBridgeRequest(**values)


def test_runner_bridge_schema_file_exists() -> None:
    assert SCHEMA_PATH.is_file()


def test_valid_request_renders_fenced_task_body() -> None:
    result = dry_run_runner_bridge(valid_request())

    assert result.status == "dry_run"
    assert result.issue_number is None
    assert result.blocked_reason is None
    assert "Repository: alanua/Skeleton" in result.dry_run_summary
    assert "Base ref: main" in result.dry_run_summary
    assert "```task\nImplement the task.\n\nReturn a concise report.\n```" in result.dry_run_summary


def test_missing_approval_blocks_none_empty_and_whitespace() -> None:
    for marker in (None, "", "   \n\t"):
        result = dry_run_runner_bridge(valid_request(approval_marker=marker))

        assert result.status == "blocked"
        assert result.issue_number is None
        assert result.blocked_reason == "approval_marker is required for runner_bridge dry-run."


def test_protected_file_overlap_blocks() -> None:
    result = dry_run_runner_bridge(
        valid_request(
            allowed_files=("core/runner_bridge.py", "docs/RUNNER_BRIDGE.md"),
            protected_files=("docs/RUNNER_BRIDGE.md",),
        )
    )

    assert result.status == "blocked"
    assert result.blocked_reason == "protected_files overlap allowed_files: docs/RUNNER_BRIDGE.md"


def test_invalid_validation_command_blocks() -> None:
    result = dry_run_runner_bridge(valid_request(validation_commands=("python3 -m pytest",)))

    assert result.status == "blocked"
    assert result.blocked_reason == "validation command is not allowlisted: python3 -m pytest"


def test_mutating_git_commands_block() -> None:
    mutating_commands = (
        "git reset --hard",
        "git checkout main",
        "git clean -fd",
        "git push",
        "git pull",
        "git merge main",
        "git commit -m test",
        "git add .",
    )

    for command in mutating_commands:
        result = dry_run_runner_bridge(valid_request(validation_commands=(command,)))

        assert result.status == "blocked"
        assert result.blocked_reason == f"validation command is not allowlisted: {command}"


def test_allowed_validation_command_patterns_pass() -> None:
    for command in sorted(ALLOWED_VALIDATION_COMMANDS):
        result = dry_run_runner_bridge(valid_request(validation_commands=(command,)))

        assert result.status == "dry_run"
        assert result.blocked_reason is None


def test_dry_run_needs_no_live_side_effects() -> None:
    with mock.patch.object(subprocess, "run") as run, mock.patch.object(
        urllib.request, "urlopen"
    ) as urlopen:
        result = dry_run_runner_bridge(valid_request())

    assert result.status == "dry_run"
    run.assert_not_called()
    urlopen.assert_not_called()
