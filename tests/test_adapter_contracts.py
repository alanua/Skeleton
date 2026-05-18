from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

ADAPTER_FILES = [
    "adapters/README.md",
    "adapters/chatgpt/START_HERE.md",
    "adapters/claude/START_HERE.md",
    "adapters/gemini/AUDITOR_CONTRACT.md",
    "adapters/codex/EXECUTOR_CONTRACT.md",
    "adapters/runner/RUNNER_CONTRACT.md",
]

REQUIRED_PHRASES = [
    ("adapters/README.md", ["BOOT_MANIFEST.yaml", "adapters do not define boot routing"]),
    ("adapters/chatgpt/START_HERE.md", ["BOOT_MANIFEST.yaml", "must not merge", "must not deploy"]),
    ("adapters/claude/START_HERE.md", ["BOOT_MANIFEST.yaml", "must not merge", "must not deploy"]),
    ("adapters/gemini/AUDITOR_CONTRACT.md", ["BOOT_MANIFEST.yaml", "read-only", "audit", "must not patch"]),
    (
        "adapters/codex/EXECUTOR_CONTRACT.md",
        ["BOOT_MANIFEST.yaml", "must not merge", "must not deploy", "must not access secrets", "bounded executor"],
    ),
    ("adapters/runner/RUNNER_CONTRACT.md", ["BOOT_MANIFEST.yaml", "read-before-write", "approval", "verification"]),
]


@pytest.mark.parametrize("rel_path", ADAPTER_FILES)
def test_adapter_contract_files_exist(rel_path: str) -> None:
    assert (ROOT / rel_path).is_file(), rel_path


@pytest.mark.parametrize("rel_path", ADAPTER_FILES)
def test_adapter_contract_files_point_to_boot_manifest(rel_path: str) -> None:
    content = (ROOT / rel_path).read_text(encoding="utf-8")
    assert "BOOT_MANIFEST.yaml" in content


@pytest.mark.parametrize(("rel_path", "phrases"), REQUIRED_PHRASES)
def test_adapter_contract_files_contain_required_phrases(rel_path: str, phrases: list[str]) -> None:
    content = (ROOT / rel_path).read_text(encoding="utf-8")

    for phrase in phrases:
        assert phrase in content
