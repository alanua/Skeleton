from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_required_commands_exist() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    for command in ["прокинься", "СК", "ДЖ", "АУД", "КОД", "БЗ", "+"]:
        assert command in commands


def test_command_modes_exist() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]
    modes = load_yaml("MODES.yaml")["modes"]

    for command, spec in commands.items():
        assert spec["mode"] in modes, command


def test_write_command_has_required_gates() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]
    gates = set(commands["БЗ"]["gates"])

    assert gates == {
        "read_target",
        "critique",
        "patch_plan",
        "explicit_approval",
        "write",
        "verify",
    }


def test_continue_command_is_bounded() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    assert commands["+"]["mode"] == "continue_approved_step"
    assert commands["+"]["rule"] == "apply_smallest_approved_next_step"
