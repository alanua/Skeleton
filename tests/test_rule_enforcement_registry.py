from __future__ import annotations

from copy import deepcopy

from core.rule_enforcement_registry import (
    ROOT,
    build_report,
    load_registry,
    load_schema,
    public_report_violations,
    required_source_universe,
    validate_registry,
)


def valid_registry() -> dict[str, object]:
    return load_registry()


def registry_rules(registry: dict[str, object]) -> list[dict[str, object]]:
    rules = registry["rules"]
    assert isinstance(rules, list)
    return rules


def registry_sources(registry: dict[str, object]) -> list[dict[str, object]]:
    sources = registry["source_coverage"]
    assert isinstance(sources, list)
    return sources


def test_schema_accepts_valid_registry() -> None:
    result = validate_registry(valid_registry())

    assert result.ok, result.errors
    assert ("active", 11) in result.lifecycle_counts
    assert ("runtime_gate", 3) in result.enforcement_counts
    assert result.effective_enforcement_gaps == ()
    assert len(required_source_universe(ROOT)) == len(registry_sources(valid_registry()))


def test_duplicate_ids_fail() -> None:
    registry = valid_registry()
    duplicate = deepcopy(registry_rules(registry)[0])
    registry_rules(registry).append(duplicate)

    result = validate_registry(registry)

    assert "duplicate rule_id: merge_requires_exact_operator_approval" in result.errors


def test_duplicate_source_coverage_entries_fail() -> None:
    registry = valid_registry()
    duplicate = deepcopy(registry_sources(registry)[0])
    registry_sources(registry).append(duplicate)

    result = validate_registry(registry)

    assert any("duplicate source_coverage entry: BOOT_MANIFEST.yaml" == error for error in result.errors)


def test_unknown_fields_fail() -> None:
    registry = valid_registry()
    registry["unexpected"] = True

    result = validate_registry(registry)

    assert any("Additional properties are not allowed" in error for error in result.errors)


def test_missing_required_source_coverage_fails() -> None:
    registry = valid_registry()
    registry["source_coverage"] = [
        item for item in registry_sources(registry) if item["source_file"] != "schemas/action_gate.schema.json"
    ]

    result = validate_registry(registry)

    assert "required source lacks source_coverage entry: schemas/action_gate.schema.json" in result.errors


def test_unknown_source_coverage_fails() -> None:
    registry = valid_registry()
    registry_sources(registry).append(
        {"source_file": "docs/UNKNOWN_RULE_SOURCE.md", "classification": "covered", "notes": "invalid"}
    )

    result = validate_registry(registry)

    assert "source_coverage entry outside required universe: docs/UNKNOWN_RULE_SOURCE.md" in result.errors


def test_missing_referenced_rule_file_fails() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["source_file"] = "missing/source.yaml"

    result = validate_registry(registry)

    assert any("references missing file" in error for error in result.errors)


def test_active_rule_with_enforcement_none_fails_as_effective_gap() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["enforcement"] = "none"

    result = validate_registry(registry)

    assert "active rule lacks verifiable enforcement: merge_requires_exact_operator_approval" in result.errors


def test_active_documentation_only_rule_fails_as_effective_gap() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["enforcement"] = "documentation_only"

    result = validate_registry(registry)

    assert "active rule lacks verifiable enforcement: merge_requires_exact_operator_approval" in result.errors


def test_missing_python_gate_entrypoint_fails_without_importing_runtime() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["gate_entrypoint"] = "core.action_gate.missing_callable"

    result = validate_registry(registry)

    assert any("gate_entrypoint Python member not found" in error for error in result.errors)


def test_missing_yaml_locator_fails() -> None:
    registry = valid_registry()
    registry_rules(registry)[5]["gate_entrypoint"] = "PROVIDER_ROUTING.yaml:missing_key"

    result = validate_registry(registry)

    assert any("gate_entrypoint locator not found: PROVIDER_ROUTING.yaml:missing_key" in error for error in result.errors)


def test_missing_source_locator_fails() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["source_locator"] = "missing_member"

    result = validate_registry(registry)

    assert any("source_locator Python member not found" in error for error in result.errors)


def test_missing_test_evidence_path_fails() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["test_evidence"] = ["tests/missing_rule_test.py"]

    result = validate_registry(registry)

    assert any("test_evidence references missing path" in error for error in result.errors)


def test_missing_audit_evidence_path_fails() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["audit_evidence"] = ["docs/MISSING_AUDIT.md"]

    result = validate_registry(registry)

    assert any("audit_evidence references missing path" in error for error in result.errors)


def test_schema_requires_audit_evidence() -> None:
    registry = valid_registry()
    del registry_rules(registry)[0]["audit_evidence"]

    result = validate_registry(registry, schema=load_schema())

    assert any("'audit_evidence' is a required property" in error for error in result.errors)


def test_overlapping_cross_owner_actions_are_contradictions() -> None:
    registry = valid_registry()
    first = deepcopy(registry_rules(registry)[0])
    first["rule_id"] = "second_merge_owner_rule"
    first["owner_component"] = "other_component"
    registry_rules(registry).append(first)

    result = validate_registry(registry)

    assert ("merge_requires_exact_operator_approval", "second_merge_owner_rule") in result.contradictory_rules
    assert any("contradictory active rules" in error for error in result.errors)


def test_overlapping_subset_actions_are_contradictions() -> None:
    registry = valid_registry()
    first = deepcopy(registry_rules(registry)[0])
    first["rule_id"] = "subset_merge_rule"
    first["protected_actions"] = ["merge_pull_request", "instruction_change"]
    registry_rules(registry).append(first)

    result = validate_registry(registry)

    assert ("merge_requires_exact_operator_approval", "subset_merge_rule") in result.contradictory_rules


def test_report_emits_explicit_review_lists() -> None:
    report = build_report(root=ROOT)

    assert "- duplicated_sources: " in report
    assert "- contradictory_sources: " in report
    assert "- dead_sources: " in report
    assert "- needs_review_sources: " in report
    assert "- needs_review_rule_ids: " in report


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
    registry_rules(registry)[0]["extra_rule_field"] = True

    result = validate_registry(registry, schema=load_schema())

    assert any("Additional properties are not allowed" in error for error in result.errors)
