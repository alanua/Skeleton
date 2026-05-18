from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "patch_plan.schema.json"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_patch_plan_schema_file_exists() -> None:
    assert SCHEMA_PATH.is_file()


def test_patch_plan_schema_required_fields_preserve_existing_fields() -> None:
    schema = load_schema()
    required = set(schema["required"])

    expected = {
        "schema",
        "target_files",
        "change_type",
        "reason",
        "current_rule_read",
        "critique",
        "minimal_patch",
        "verification",
        "approval_required",
    }

    assert expected.issubset(required)


def test_patch_plan_schema_required_fields_include_operator_approval() -> None:
    schema = load_schema()

    assert "operator_approval" in schema["required"]


def test_patch_plan_schema_const_remains_v1() -> None:
    schema = load_schema()

    assert schema["properties"]["schema"]["const"] == "skeleton.patch_plan.v1"
