from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHATGPT_PROMPT = ROOT / "adapters/chatgpt/SYSTEM_PROMPT.md"
GEMINI_PROMPT = ROOT / "adapters/gemini/SYSTEM_PROMPT.md"


def test_chatgpt_system_prompt_exists() -> None:
    assert CHATGPT_PROMPT.is_file()


def test_gemini_system_prompt_exists() -> None:
    assert GEMINI_PROMPT.is_file()


def test_chatgpt_mentions_boot_manifest() -> None:
    assert "BOOT_MANIFEST.yaml" in CHATGPT_PROMPT.read_text(encoding="utf-8")


def test_gemini_mentions_boot_manifest() -> None:
    assert "BOOT_MANIFEST.yaml" in GEMINI_PROMPT.read_text(encoding="utf-8")


def test_chatgpt_mentions_must_not_merge() -> None:
    assert "must not merge" in CHATGPT_PROMPT.read_text(encoding="utf-8")


def test_chatgpt_mentions_must_not_deploy() -> None:
    assert "must not deploy" in CHATGPT_PROMPT.read_text(encoding="utf-8")


def test_chatgpt_mentions_operator_approval() -> None:
    assert "operator approval" in CHATGPT_PROMPT.read_text(encoding="utf-8")


def test_chatgpt_mentions_patch_plan() -> None:
    assert "PatchPlan" in CHATGPT_PROMPT.read_text(encoding="utf-8")


def test_chatgpt_mentions_single_entrypoint() -> None:
    assert "Single entrypoint" in CHATGPT_PROMPT.read_text(encoding="utf-8")


def test_gemini_mentions_read_only() -> None:
    assert "read-only" in GEMINI_PROMPT.read_text(encoding="utf-8")


def test_gemini_mentions_must_not_patch() -> None:
    assert "must not patch" in GEMINI_PROMPT.read_text(encoding="utf-8")


def test_gemini_mentions_audit() -> None:
    assert "audit" in GEMINI_PROMPT.read_text(encoding="utf-8")
