from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_required_commands_exist() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    for command in ["прокинься", "СК", "ДЖ", "АУД", "КОД", "БЗ", "зафіксуй", "фіксуй", "+"]:
        assert command in commands


def test_command_modes_exist() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]
    modes = load_yaml("MODES.yaml")["modes"]

    for command, spec in commands.items():
        assert spec["mode"] in modes, command


def test_commands_have_typed_loads_produces_and_writes() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    for command, spec in commands.items():
        assert spec["loads"], command
        assert spec["produces"], command
        assert isinstance(spec["writes"], str), command


def test_boot_command_produces_boot_report_and_writes_none() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    assert commands["прокинься"]["produces"] == ["BootReport"]
    assert commands["прокинься"]["writes"] == "none"


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


def test_fixuy_commands_record_without_extra_plus_but_keep_risky_actions_gated() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    for command in ["зафіксуй", "фіксуй"]:
        spec = commands[command]
        means = set(spec["means"])

        assert spec["mode"] == "persist_request"
        assert spec["writes"] == "routed_durable_storage_after_write_gate"
        assert "fixuy_is_enough_approval_to_record_stated_rule_after_classification" in means
        assert "route_public_safe_durable_to_GitHub_canon_review_without_extra_plus" in means
        assert "route_private_context_privately" in means
        assert "secrets_never_to_chat_GitHub_plain_Drive" in means
        assert "risky_actions_merge_deploy_runtime_secrets_destructive_require_separate_explicit_approval" in means
        assert (
            "report_to_Oleksii_human_readable_clear_person_to_person_technical_details_only_when_needed_or_requested"
            in means
        )
