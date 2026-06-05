from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "OPERATOR_RULES.yaml"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def split_source_ref(source_ref: str) -> tuple[Path, str | None]:
    path_text, separator, fragment = source_ref.partition(":")

    assert path_text, source_ref
    if separator:
        assert fragment, source_ref
        return ROOT / path_text, fragment

    return ROOT / path_text, None


def operator_rule_source_refs(registry: dict) -> list[str]:
    return [source_ref for rule in registry["rules"] for source_ref in rule["source_refs"]]


def yaml_fragment_resolves(value: object, fragment: str) -> bool:
    if isinstance(value, dict):
        return any(
            str(key) == fragment or yaml_fragment_resolves(child, fragment)
            for key, child in value.items()
        )

    if isinstance(value, list):
        return any(yaml_fragment_resolves(item, fragment) for item in value)

    return isinstance(value, str) and fragment in value


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

    notes = registry["enforcement_model"]["notes"]
    assert "Severity block is a policy-level classification for unsafe actions." in notes
    assert "Block rules are not automatic runtime hooks while runtime_enforcement is false." in notes
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


def test_operator_rule_source_ref_files_exist() -> None:
    registry = load_yaml(REGISTRY_PATH)

    for source_ref in operator_rule_source_refs(registry):
        path, _fragment = split_source_ref(source_ref)
        assert path.is_file(), source_ref


def test_operator_rule_yaml_source_ref_fragments_resolve() -> None:
    registry = load_yaml(REGISTRY_PATH)

    for source_ref in operator_rule_source_refs(registry):
        path, fragment = split_source_ref(source_ref)
        if fragment is None or path.suffix not in {".yaml", ".yml"}:
            continue

        assert yaml_fragment_resolves(load_yaml(path), fragment), source_ref


def test_boot_manifest_includes_operator_rules_registry() -> None:
    manifest = load_yaml(ROOT / "BOOT_MANIFEST.yaml")
    read_order = manifest["read_order"]

    assert "OPERATOR_RULES.yaml" in read_order
    assert read_order.index("COMMANDS.yaml") < read_order.index("OPERATOR_RULES.yaml")
    assert read_order.index("OPERATOR_RULES.yaml") < read_order.index("MODES.yaml")
