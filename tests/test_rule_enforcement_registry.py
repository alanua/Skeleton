from __future__ import annotations

from copy import deepcopy
import pytest

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
    assert ("active", 8) in result.lifecycle_counts
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
    registry_rules(registry)[0]["audit_evidence"] = ["schemas/action_gate.schema.json:missing_key"]

    result = validate_registry(registry)

    assert any("audit_evidence locator not found: schemas/action_gate.schema.json:missing_key" in error for error in result.errors)


def test_missing_source_locator_fails() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["source_locator"] = "missing_member"

    result = validate_registry(registry)

    assert any("source_locator Python member not found" in error for error in result.errors)


def test_missing_test_evidence_path_fails() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["test_evidence"] = ["tests/missing_rule_test.py:test_missing_rule"]

    result = validate_registry(registry)

    assert any("test_evidence references missing path" in error for error in result.errors)


def test_missing_audit_evidence_path_fails() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["audit_evidence"] = ["docs/MISSING_AUDIT.md"]

    result = validate_registry(registry)

    assert any("audit_evidence references missing path" in error for error in result.errors)


def test_empty_registry_and_schema_do_not_fall_back_to_defaults() -> None:
    result = validate_registry({}, schema=load_schema())
    arbitrary_result = validate_registry({"anything": "would otherwise pass"}, schema={})

    assert not result.ok
    assert any("'schema' is a required property" in error for error in result.errors)
    assert not arbitrary_result.ok
    assert "schema mapping is empty or structurally invalid" in arbitrary_result.errors


def test_schema_validation_requires_gate_tokens_and_evidence() -> None:
    registry = valid_registry()
    rule = registry_rules(registry)[0]
    rule["enforcement"] = "schema_validation"
    rule["gate_entrypoint"] = ""
    rule["reason_tokens"] = []
    rule["test_evidence"] = []

    result = validate_registry(registry)

    assert "active rule lacks verifiable enforcement: merge_requires_exact_operator_approval" in result.errors
    assert any("verifiable rule lacks gate_entrypoint" in error for error in result.errors)
    assert any("verifiable rule lacks stable reason token" in error for error in result.errors)
    assert any("verifiable rule lacks test evidence" in error for error in result.errors)


def test_stable_reason_tokens_require_concrete_evidence() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["reason_tokens"] = ["token_present_only_in_registry"]

    result = validate_registry(registry)

    assert "active rule lacks verifiable enforcement: merge_requires_exact_operator_approval" in result.errors
    assert (
        "merge_requires_exact_operator_approval reason token lacks concrete evidence: "
        "token_present_only_in_registry"
    ) in result.errors


def test_active_route_validation_rejects_passive_config_entrypoint() -> None:
    registry = valid_registry()
    rule = registry_rules(registry)[0]
    rule["enforcement"] = "route_validation"
    rule["gate_entrypoint"] = "MEMORY_ROUTING.yaml:risky_action_rule"

    result = validate_registry(registry)

    assert "active rule lacks verifiable enforcement: merge_requires_exact_operator_approval" in result.errors
    assert any("route_validation lacks callable or route binding gate_entrypoint" in error for error in result.errors)


def test_test_evidence_requires_specific_test_callable() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["test_evidence"] = ["tests/test_action_gate.py"]

    result = validate_registry(registry)

    assert any("does not match" in error for error in result.errors)
    assert any("test_evidence must reference a test callable" in error for error in result.errors)


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


def test_overlapping_gate_or_reason_semantics_are_contradictions() -> None:
    registry = valid_registry()
    first = deepcopy(registry_rules(registry)[0])
    first["rule_id"] = "different_gate_merge_rule"
    first["gate_entrypoint"] = "core.action_gate.ActionRequest"
    registry_rules(registry).append(first)
    second = deepcopy(registry_rules(registry)[0])
    second["rule_id"] = "different_reason_merge_rule"
    second["reason_tokens"] = ["different_stable_reason"]
    registry_rules(registry).append(second)

    result = validate_registry(registry)

    assert ("different_gate_merge_rule", "merge_requires_exact_operator_approval") in result.contradictory_rules
    assert ("different_reason_merge_rule", "merge_requires_exact_operator_approval") in result.contradictory_rules


def test_active_to_active_supersession_does_not_suppress_contradictions() -> None:
    registry = valid_registry()
    first = deepcopy(registry_rules(registry)[0])
    first["rule_id"] = "superseding_merge_rule"
    first["gate_entrypoint"] = "core.action_gate.ActionRequest"
    first["supersedes"] = ["merge_requires_exact_operator_approval"]
    registry_rules(registry)[0]["superseded_by"] = ["superseding_merge_rule"]
    registry_rules(registry).append(first)

    result = validate_registry(registry)

    assert ("merge_requires_exact_operator_approval", "superseding_merge_rule") in result.contradictory_rules
    assert any("superseded_by requires lifecycle superseded" in error for error in result.errors)
    assert any("supersedes non-superseded rule" in error for error in result.errors)
    assert any("active-to-active supersession is invalid" in error for error in result.errors)


def test_one_way_supersession_reference_fails() -> None:
    registry = valid_registry()
    delegated = next(rule for rule in registry_rules(registry) if rule["rule_id"] == "delegated_merge_review_only")
    delegated["supersedes"] = []

    result = validate_registry(registry)

    assert (
        "legacy_merge_review_overlap superseded_by lacks reciprocal supersedes on "
        "delegated_merge_review_only"
    ) in result.errors


def test_unknown_supersession_reference_fails() -> None:
    registry = valid_registry()
    legacy = next(rule for rule in registry_rules(registry) if rule["rule_id"] == "legacy_merge_review_overlap")
    legacy["superseded_by"] = ["missing_rule"]

    result = validate_registry(registry)

    assert "legacy_merge_review_overlap superseded_by unknown rule_id: missing_rule" in result.errors


def test_superseded_by_requires_superseded_lifecycle() -> None:
    registry = valid_registry()
    legacy = next(rule for rule in registry_rules(registry) if rule["rule_id"] == "legacy_merge_review_overlap")
    legacy["lifecycle"] = "advisory"

    result = validate_registry(registry)

    assert "legacy_merge_review_overlap superseded_by requires lifecycle superseded" in result.errors


def test_covered_source_requires_rule_link_or_non_rule_rationale() -> None:
    registry = valid_registry()
    source = next(item for item in registry_sources(registry) if item["source_file"] == "BOOT_MANIFEST.yaml")
    del source["non_rule_rationale"]

    result = validate_registry(registry)

    assert "covered source lacks linked rule or non_rule_rationale: BOOT_MANIFEST.yaml" in result.errors


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


@pytest.mark.parametrize(
    "value",
    [
        "/home/alice/project",
        "/root/private.txt",
        r"C:\Users\alice\secret.txt",
        "alice@example.com",
        "Authorization: Bearer abcdef123456",
        "api_key=abcdef123456",
        "ghp_abcdefghijklmnopqrstuvwxyz",
        "github_pat_abcdefghijklmnopqrstuvwxyz",
        "https://internal.example/private/task",
        "customer_id=customer-123",
        "owner=/home/alice/project",
        "raw payload",
        "environment value",
        "SKELETON_TG_BOT",
    ],
)
def test_public_report_privacy_patterns_cover_each_forbidden_class(value: str) -> None:
    assert public_report_violations(value), value


def test_report_generation_is_byte_identical_for_reordered_semantic_input() -> None:
    registry = valid_registry()
    reordered = deepcopy(registry)
    reordered["issue_refs"] = list(reversed(reordered["issue_refs"]))
    reordered["rules"] = list(reversed(reordered["rules"]))
    reordered["next_stages"] = list(reversed(reordered["next_stages"]))

    assert build_report(registry, root=ROOT) == build_report(reordered, root=ROOT)


def test_schema_rejects_unknown_rule_field() -> None:
    registry = valid_registry()
    registry_rules(registry)[0]["extra_rule_field"] = True

    result = validate_registry(registry, schema=load_schema())

    assert any("Additional properties are not allowed" in error for error in result.errors)
