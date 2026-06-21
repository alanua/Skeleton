from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from core.rule_enforcement_registry import (
    ROOT,
    build_report,
    load_registry,
    load_schema,
    public_report_violations,
    validate_registry,
)


def valid_registry() -> dict[str, object]:
    return load_registry()


def test_schema_accepts_valid_registry() -> None:
    result = validate_registry(valid_registry())

    assert result.ok, result.errors
    assert ("active", 14) in result.lifecycle_counts
    assert ("runtime_gate", 4) in result.enforcement_counts


def test_duplicate_ids_fail() -> None:
    registry = valid_registry()
    rules = registry["rules"]
    assert isinstance(rules, list)
    duplicate = deepcopy(rules[0])
    rules.append(duplicate)

    result = validate_registry(registry)

    assert "duplicate rule_id: merge_requires_exact_operator_approval" in result.errors


def test_unknown_fields_fail() -> None:
    registry = valid_registry()
    registry["unexpected"] = True

    result = validate_registry(registry)

    assert any("Additional properties are not allowed" in error for error in result.errors)


def test_missing_referenced_file_fails() -> None:
    registry = valid_registry()
    rules = registry["rules"]
    assert isinstance(rules, list)
    rules[0]["source_file"] = "missing/source.yaml"

    result = validate_registry(registry)

    assert any("references missing file" in error for error in result.errors)


def test_active_rule_with_enforcement_none_fails() -> None:
    registry = valid_registry()
    rules = registry["rules"]
    assert isinstance(rules, list)
    rules[0]["enforcement"] = "none"

    result = validate_registry(registry)

    assert "active rule uses enforcement=none: merge_requires_exact_operator_approval" in result.errors


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("gate_entrypoint", "", "runtime/preflight rule lacks gate_entrypoint"),
        ("reason_tokens", [], "runtime/preflight rule lacks stable reason token"),
    ],
)
def test_runtime_gate_without_entrypoint_or_reason_token_fails(
    field: str,
    value: object,
    expected: str,
) -> None:
    registry = valid_registry()
    rules = registry["rules"]
    assert isinstance(rules, list)
    rules[0][field] = value

    result = validate_registry(registry)

    assert any(expected in error for error in result.errors)


def test_approval_rule_without_exact_scope_fails() -> None:
    registry = valid_registry()
    rules = registry["rules"]
    assert isinstance(rules, list)
    rules[0]["approval_scope"] = "none"

    result = validate_registry(registry)

    assert "merge_requires_exact_operator_approval protected action lacks exact approval_scope" in result.errors


def test_contradictory_active_rules_are_surfaced() -> None:
    registry = valid_registry()
    rules = registry["rules"]
    assert isinstance(rules, list)
    first = deepcopy(rules[0])
    first["rule_id"] = "merge_requires_second_gate"
    first["gate_entrypoint"] = "other.gate"
    rules.append(first)

    result = validate_registry(registry)

    assert (
        "merge_requires_exact_operator_approval",
        "merge_requires_second_gate",
    ) in result.contradictory_rules
    assert any("contradictory active rules" in error for error in result.errors)


def test_deterministic_report_generation(tmp_path: Path) -> None:
    report_a = build_report(root=ROOT)
    report_b = build_report(root=ROOT)

    assert report_a == report_b
    assert report_a.startswith("# Rule Enforcement Matrix\n")


def test_generated_report_contains_no_private_or_raw_values() -> None:
    report = build_report(root=ROOT)

    assert public_report_violations(report) == ()
    forbidden = (
        "/home/agent/",
        "SKELETON_TG",
        "raw payload",
        "environment value",
        "secret=",
        "token=",
    )
    assert not any(value in report for value in forbidden)


def test_schema_rejects_unknown_rule_field() -> None:
    registry = valid_registry()
    rules = registry["rules"]
    assert isinstance(rules, list)
    rules[0]["extra_rule_field"] = True

    result = validate_registry(registry, schema=load_schema())

    assert any("Additional properties are not allowed" in error for error in result.errors)
