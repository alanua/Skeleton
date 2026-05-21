from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "HELPER_REGISTRY.yaml"
REQUIRED_HELPERS = {
    "chatgpt",
    "runner",
    "codex",
    "gemini",
    "telegram_bot",
    "notebooklm",
    "claude",
    "operator",
}


def load_registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def test_registry_file_exists() -> None:
    assert REGISTRY_PATH.is_file()


def test_registry_has_version() -> None:
    assert load_registry()["version"] == "1.0.0"


def test_registry_has_helpers_key() -> None:
    helpers = load_registry()["helpers"]
    assert isinstance(helpers, dict)
    assert helpers


def test_required_helpers_present() -> None:
    assert REQUIRED_HELPERS <= load_registry()["helpers"].keys()


def test_every_helper_has_role_and_trust() -> None:
    for helper in load_registry()["helpers"].values():
        assert helper["role"]
        assert helper["trust"]


def test_every_non_operator_helper_has_can_and_cannot() -> None:
    for helper_id, helper in load_registry()["helpers"].items():
        if helper_id == "operator":
            continue

        assert helper["can"]
        assert helper["cannot"]


def test_operator_can_approve_merge() -> None:
    operator = load_registry()["helpers"]["operator"]
    assert "approve_merge" in operator["can"]


def test_codex_cannot_merge() -> None:
    codex = load_registry()["helpers"]["codex"]
    assert "merge" in codex["cannot"]


def test_chatgpt_cannot_merge() -> None:
    chatgpt = load_registry()["helpers"]["chatgpt"]
    assert "merge" in chatgpt["cannot"]


def test_runner_cannot_deploy() -> None:
    runner = load_registry()["helpers"]["runner"]
    assert "deploy" in runner["cannot"]


def test_runner_and_codex_cannot_expand_scope_autonomously() -> None:
    helpers = load_registry()["helpers"]
    assert "autonomous_scope_expansion" in helpers["runner"]["cannot"]
    assert "autonomous_scope_expansion" in helpers["codex"]["cannot"]
