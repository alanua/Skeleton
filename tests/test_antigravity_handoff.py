from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.antigravity_handoff import AntigravityHandoff, build_handoff_pack, write_handoff_pack


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "antigravity_handoff.py"


def test_handoff_pack_includes_required_task_sections() -> None:
    pack = build_handoff_pack(
        AntigravityHandoff(
            repository="alanua/Skeleton",
            base_branch="main",
            source_issue="438",
            allowed_files=("scripts/antigravity_handoff.py", "tests/test_antigravity_handoff.py"),
            forbidden_files=("scripts/runner_poll_github_tasks.py",),
            validation_commands=(
                "python3 -m pytest -q tests/test_antigravity_handoff.py",
                "python3 -m pytest -q",
            ),
        )
    )

    for section in [
        "Context",
        "Task",
        "Allowed files",
        "Forbidden files",
        "Forbidden actions",
        "Validation commands",
        "Pull request expectations",
        "Safety gates",
        "Required final output from Antigravity",
        "Merge boundary",
    ]:
        assert section in pack

    assert "- Repository: alanua/Skeleton" in pack
    assert "- Base branch: main" in pack
    assert "- Source issue: #438" in pack
    assert "- scripts/antigravity_handoff.py" in pack
    assert "- tests/test_antigravity_handoff.py" in pack
    assert "- scripts/runner_poll_github_tasks.py" in pack
    assert "- `python3 -m pytest -q tests/test_antigravity_handoff.py`" in pack
    assert "- `python3 -m pytest -q`" in pack


def test_handoff_pack_defaults_to_safe_external_pr_boundaries() -> None:
    pack = build_handoff_pack(
        AntigravityHandoff(
            repository="alanua/Skeleton",
            base_branch="main",
            source_issue="#438",
            allowed_files=("scripts/antigravity_handoff.py",),
            validation_commands=("python3 -m pytest -q",),
        )
    )

    assert "No deploy." in pack
    assert "No secrets or credential handling." in pack
    assert "No runtime service changes" in pack
    assert "No merge, direct push to protected branches, or force push." in pack
    assert "No target repository execution beyond local validation commands explicitly listed in this pack." in pack
    assert "No private data handling." in pack
    assert "No Antigravity API integration." in pack
    assert "Open a pull request only; do not merge it." in pack
    assert "ChatGPT PR review plus Telegram-approved Runner merge" in pack


def test_handoff_pack_rejects_missing_explicit_inputs() -> None:
    with pytest.raises(ValueError, match="allowed_files"):
        build_handoff_pack(
            AntigravityHandoff(
                repository="alanua/Skeleton",
                base_branch="main",
                source_issue="438",
                allowed_files=(),
                validation_commands=("python3 -m pytest -q",),
            )
        )

    with pytest.raises(ValueError, match="validation_commands"):
        build_handoff_pack(
            AntigravityHandoff(
                repository="alanua/Skeleton",
                base_branch="main",
                source_issue="438",
                allowed_files=("scripts/antigravity_handoff.py",),
                validation_commands=(),
            )
        )


def test_write_handoff_pack_supports_explicit_output(tmp_path: Path) -> None:
    output = tmp_path / "handoff.txt"
    handoff = AntigravityHandoff(
        repository="alanua/Skeleton",
        base_branch="main",
        source_issue="438",
        allowed_files=("scripts/antigravity_handoff.py",),
        validation_commands=("python3 -m pytest -q",),
    )

    written = write_handoff_pack(handoff, output)

    assert written == output
    assert output.read_text(encoding="utf-8") == build_handoff_pack(handoff)


def test_cli_generates_pack_to_stdout_without_network() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repository",
            "alanua/Skeleton",
            "--base-branch",
            "main",
            "--source-issue",
            "438",
            "--allowed-file",
            "scripts/antigravity_handoff.py",
            "--validation-command",
            "python3 -m pytest -q tests/test_antigravity_handoff.py",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "Antigravity External Executor Handoff Pack" in result.stdout
    assert "- Source issue: #438" in result.stdout
    assert "Pull request URL." in result.stdout
    assert result.stderr == ""
