from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "AUFMASS_PRIVATE_REVIEW_ROUTE.md"
SCHEMA_PATH = ROOT / "schemas" / "aufmass_private_pilot_manifest.schema.json"
ROUTE_STAGES = [
    "input_sources",
    "extracted_candidates",
    "room_review_table",
    "operator_corrections",
    "private_exports",
    "public_safe_lessons",
]


def load_schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_loads_as_json() -> None:
    schema = load_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "skeleton.aufmass_private_pilot_manifest.schema.json"


def test_schema_requires_public_safety() -> None:
    schema = load_schema()

    assert "public_safety" in schema["required"]  # type: ignore[operator]


def test_schema_disallows_additional_properties() -> None:
    schema = load_schema()
    defs = schema["$defs"]  # type: ignore[index]

    assert schema["additionalProperties"] is False
    assert defs["private_ref"]["additionalProperties"] is False
    assert defs["public_safety"]["additionalProperties"] is False


def test_schema_route_stage_enum_matches_docs() -> None:
    schema = load_schema()
    route_stage = schema["$defs"]["route_stage"]  # type: ignore[index]
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert route_stage["enum"] == ROUTE_STAGES
    for stage in ROUTE_STAGES:
        assert f"- `{stage}`" in doc


def test_schema_mentions_no_drive_urls_or_file_ids() -> None:
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8").lower()

    for forbidden in ("drive.google", "drive url", "file id", "folder id"):
        assert forbidden not in schema_text


def test_schema_contains_no_real_private_data_examples() -> None:
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8").lower()

    for forbidden in ("example", "address", "customer", "quantity", "drawing"):
        assert forbidden not in schema_text
