from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def joined(value: object) -> str:
    return yaml.safe_dump(value, allow_unicode=True).lower()


def test_operator_command_files_parse_and_modes_match() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]
    modes = load_yaml("MODES.yaml")["modes"]

    for command, spec in commands.items():
        assert spec["mode"] in modes, command


def test_persist_command_and_mode_exist() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]
    modes = load_yaml("MODES.yaml")["modes"]

    assert commands["зафіксуй"]["mode"] == "persist_request"
    assert "persist_request" in modes


def test_persist_command_documents_required_boundaries() -> None:
    command = joined(load_yaml("COMMANDS.yaml")["commands"]["зафіксуй"])

    for expected in [
        "classify",
        "durable",
        "write_gate",
        "private",
        "secrets",
    ]:
        assert expected in command


def test_batch_rule_exists_and_is_bounded() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]
    batch = commands.get("пакетно") or commands.get("batch_same_type_tasks")

    assert batch is not None
    assert batch["mode"] == "batch_continue_approved_route"

    content = joined(batch)
    for expected in ["same_type", "scope", "risk", "gate"]:
        assert expected in content


def test_operator_commands_doc_covers_persistence_and_batch_rules() -> None:
    doc = (ROOT / "docs/OPERATOR_COMMANDS.md").read_text(encoding="utf-8").lower()

    for expected in [
        "зафіксуй",
        "durable",
        "chatgpt memory",
        "commands.yaml",
        "modes.yaml",
        "memory_routing.yaml",
        "source_registry.yaml",
        "batch processing",
        "approval gates",
    ]:
        assert expected in doc
