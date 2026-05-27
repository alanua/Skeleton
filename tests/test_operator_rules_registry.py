from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "OPERATOR_RULES.yaml"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_operator_rules_registry_exists_and_parses() -> None:
    assert REGISTRY_PATH.is_file()
    registry = load_yaml(REGISTRY_PATH)

    assert registry["schema"] == "skeleton.operator_rules.v1"
    assert registry["status"] == "STATIC_REGISTRY_STAGE_1"
    assert registry["purpose"]
    assert registry["rules"]


def test_operator_rules_registry_is_reference_not_runtime_gate() -> None:
    registry = load_yaml(REGISTRY_PATH)

    assert registry["enforcement_model"]["stage"] == "registry_only"
    assert registry["enforcement_model"]["runtime_enforcement"] is False
    assert registry["enforcement_model"]["python_gate_module"] == "none"
    assert not (ROOT / "core/operator_rule_gate.py").exists()


def test_operator_rule_ids_are_unique_and_severities_are_allowlisted() -> None:
    registry = load_yaml(REGISTRY_PATH)
    allowed = set(registry["allowed_severities"])
    rule_ids = [rule["id"] for rule in registry["rules"]]

    assert len(rule_ids) == len(set(rule_ids))
    assert allowed == {"block", "rewrite", "warn", "log"}
    assert {rule["severity"] for rule in registry["rules"]} <= allowed


def test_operator_rules_have_required_human_fields() -> None:
    registry = load_yaml(REGISTRY_PATH)

    for rule in registry["rules"]:
        assert rule["id"]
        assert rule["severity"]
        assert rule["scope"]
        assert rule["source_refs"]
        assert rule["plain_language"]
        assert rule["check"]


def test_boot_manifest_includes_operator_rules_registry() -> None:
    manifest = load_yaml(ROOT / "BOOT_MANIFEST.yaml")
    read_order = manifest["read_order"]

    assert "OPERATOR_RULES.yaml" in read_order
    assert read_order.index("COMMANDS.yaml") < read_order.index("OPERATOR_RULES.yaml")
    assert read_order.index("OPERATOR_RULES.yaml") < read_order.index("MODES.yaml")
