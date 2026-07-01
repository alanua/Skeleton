from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from core.canonical_memory_manifest import (
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


def test_valid_operator_preference_manifest_passes() -> None:
    manifest = load_manifest()

    result = validate_canonical_memory_manifest(manifest)

    assert result.ok is True
    assert result.errors == []
    assert canonical_manifest_from_dict(manifest)["key"] == "fast_autonomous_execution_v1"


def test_integrity_hash_is_deterministic_and_verified() -> None:
    manifest = load_manifest()

    assert canonical_manifest_integrity_hash(manifest) == manifest["integrity_hash"]
    assert canonical_manifest_integrity_hash(deepcopy(manifest)) == manifest["integrity_hash"]

    tampered = deepcopy(manifest)
    tampered["record"]["operating_rules"].append("Add an unapproved rule.")  # type: ignore[index]
    result = validate_canonical_memory_manifest(tampered)

    assert result.ok is False
    assert ("$.integrity_hash", "integrity_hash_mismatch") in [
        (issue.path, issue.code) for issue in result.errors
    ]


def test_rejects_raw_transcript_private_values_and_local_paths() -> None:
    manifest = load_manifest()
    manifest["raw_transcript"] = "raw chat excerpt"
    manifest["record"]["operating_rules"].append("Read /tmp/private.db")  # type: ignore[index]
    manifest["provenance"]["secret"] = "token-value"  # type: ignore[index]

    result = validate_canonical_memory_manifest(manifest)

    assert result.ok is False
    assert "forbidden_field" in {issue.code for issue in result.errors}
    assert "private_or_raw_value" in {issue.code for issue in result.errors}


def test_rejects_unsupported_namespace_missing_provenance_and_sqlite_write_intent() -> None:
    manifest = load_manifest()
    manifest["namespace"] = "skeleton.private_operator_preferences"
    manifest["authority"] = "canonical_sqlite"
    del manifest["provenance"]

    result = validate_canonical_memory_manifest(manifest)

    assert result.ok is False
    assert {
        "unsupported_namespace",
        "direct_sqlite_write_intent",
        "missing_required",
        "integrity_hash_mismatch",
    }.issubset({issue.code for issue in result.errors})


def test_schema_matches_manifest_constants() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.canonical_memory_manifest.schema.json"
    assert schema["properties"]["schema"]["const"] == CANONICAL_MEMORY_MANIFEST_SCHEMA
    assert schema["properties"]["namespace"]["const"] == "skeleton.operator_preferences"
    assert schema["properties"]["authority"]["const"] == "candidate_manifest_only"


def test_docs_state_import_remains_gated() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "manifest-only" in doc
    assert "does not write to canonical SQLite" in doc
    assert "approved GitHub issue comment reference `4846756659`" in doc

