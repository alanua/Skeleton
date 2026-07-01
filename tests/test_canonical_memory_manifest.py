from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from core.canonical_memory_manifest import (
    APPROVED_OPERATOR_RULE_CATEGORIES,
    APPROVED_OPERATOR_RULE_COUNT,
    APPROVED_OPERATOR_RULE_IDS,
    APPROVED_OPERATOR_RULE_SPEC,
    CANONICAL_MEMORY_MANIFEST_SCHEMA,
    canonical_manifest_from_dict,
    canonical_manifest_integrity_hash,
    validate_canonical_memory_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "canonical_memory" / "operator_preferences_fast_autonomous_execution_v1.json"
SCHEMA_PATH = ROOT / "schemas" / "canonical_memory_manifest.schema.json"
DOC_PATH = ROOT / "docs" / "CANONICAL_MEMORY_MIGRATION.md"


def load_manifest() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def rehash(manifest: dict[str, object]) -> None:
    manifest["integrity_hash"] = canonical_manifest_integrity_hash(manifest)


def codes(manifest: dict[str, object]) -> set[str]:
    return {issue.code for issue in validate_canonical_memory_manifest(manifest).errors}


def test_valid_manifest_and_exact_readback() -> None:
    manifest = load_manifest()
    assert validate_canonical_memory_manifest(manifest).ok is True
    assert canonical_manifest_from_dict(manifest) == manifest
    assert canonical_manifest_integrity_hash(manifest) == manifest["integrity_hash"]


def test_changed_statement_with_recomputed_hash_fails_closed() -> None:
    manifest = load_manifest()
    manifest["record"]["operating_rules"][0]["statement"] = "Changed."  # type: ignore[index]
    rehash(manifest)
    assert "operating_rule_statement_mismatch" in codes(manifest)
    assert "integrity_hash_mismatch" not in codes(manifest)


def test_duplicate_id_and_category_fail_closed() -> None:
    manifest = load_manifest()
    rules = manifest["record"]["operating_rules"]  # type: ignore[index]
    rules[1]["id"] = rules[0]["id"]
    rules[2]["category"] = rules[0]["category"]
    rehash(manifest)
    result = codes(manifest)
    assert "duplicate_operating_rule_id" in result
    assert "duplicate_operating_rule_category" in result


def test_missing_and_extra_rules_fail_closed() -> None:
    missing = load_manifest()
    missing["record"]["operating_rules"].pop()  # type: ignore[index]
    rehash(missing)
    assert {"operating_rule_count_mismatch", "missing_approved_operating_rule"} <= codes(missing)

    extra = load_manifest()
    extra["record"]["operating_rules"].append(  # type: ignore[index]
        {"id": "rule-extra", "category": "extra_category", "statement": "Extra."}
    )
    rehash(extra)
    assert {
        "operating_rule_count_mismatch",
        "unsupported_operating_rule_id",
        "extra_operating_rule",
    } <= codes(extra)


def test_unsupported_fields_fail_closed_at_every_level() -> None:
    paths = (
        (),
        ("provenance",),
        ("supersession",),
        ("record",),
        ("record", "operating_rules", 0),
    )
    for path in paths:
        manifest = load_manifest()
        target: object = manifest
        for part in path:
            target = target[part]  # type: ignore[index]
        target["unexpected"] = True  # type: ignore[index]
        rehash(manifest)
        assert "unsupported_field" in codes(manifest)


def test_private_raw_and_direct_write_guards_remain_closed() -> None:
    manifest = load_manifest()
    manifest["raw_" + "transcript"] = "raw " + "chat excerpt"
    manifest["record"]["operating_rules"].append(  # type: ignore[index]
        "Read /" + "tmp/" + "private" + ".db"
    )
    manifest["provenance"]["sec" + "ret"] = "to" + "ken-value"  # type: ignore[index]
    assert {"forbidden_field", "private_or_raw_value"} <= codes(manifest)

    manifest = load_manifest()
    manifest["namespace"] = "skeleton.private_operator_preferences"
    manifest["scope"] = "private_operator_working_style"
    manifest["authority"] = "canonical_" + "sqlite"
    del manifest["provenance"]["approval_ref"]  # type: ignore[index]
    rehash(manifest)
    assert {
        "unsupported_namespace",
        "unsupported_scope",
        "direct_sqlite_write_intent",
        "missing_required",
    } <= codes(manifest)


def test_schema_matches_exact_approved_rule_specification() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    operating_rules = schema["properties"]["record"]["properties"]["operating_rules"]
    approved = {
        (
            item["properties"]["id"]["const"],
            item["properties"]["category"]["const"],
            item["properties"]["statement"]["const"],
        )
        for item in operating_rules["items"]["oneOf"]
    }
    assert schema["properties"]["schema"]["const"] == CANONICAL_MEMORY_MANIFEST_SCHEMA
    assert schema["additionalProperties"] is False
    assert operating_rules["minItems"] == APPROVED_OPERATOR_RULE_COUNT
    assert operating_rules["maxItems"] == APPROVED_OPERATOR_RULE_COUNT
    assert operating_rules["uniqueItems"] is True
    assert approved == set(APPROVED_OPERATOR_RULE_SPEC)


def test_manifest_contains_every_exact_approved_rule() -> None:
    rules = canonical_manifest_from_dict(load_manifest())["record"]["operating_rules"]  # type: ignore[index]
    actual = {(rule["id"], rule["category"], rule["statement"]) for rule in rules}
    assert actual == set(APPROVED_OPERATOR_RULE_SPEC)
    assert {rule["id"] for rule in rules} == APPROVED_OPERATOR_RULE_IDS
    assert {rule["category"] for rule in rules} == APPROVED_OPERATOR_RULE_CATEGORIES
    assert len(rules) == APPROVED_OPERATOR_RULE_COUNT


def test_docs_keep_import_gated() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert "manifest-only" in doc
    assert "does not write to canonical SQLite" in doc
    assert "approved GitHub issue comment reference `4846756659`" in doc
    assert "exact 12-rule specification" in doc
