from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "AUFMASS_SECTION_OPENING_HEIGHT_REVIEW.md"
SCHEMA_DIR = ROOT / "schemas"

SECTION_ALIGNMENT_COLUMNS = [
    "section_id",
    "section_name",
    "section_source_ref",
    "section_source_layer",
    "room_id",
    "room_name",
    "wall_id",
    "wall_ref",
    "alignment_basis",
    "alignment_status",
    "operator_note",
    "export_ready",
]
OPENING_HEIGHT_COLUMNS = [
    "opening_id",
    "opening_name",
    "opening_kind",
    "room_id",
    "room_name",
    "wall_id",
    "wall_ref",
    "section_id",
    "section_source_ref",
    "opening_width_m",
    "opening_height_m",
    "height_source",
    "section_alignment_status",
    "review_status",
    "operator_note",
    "export_ready",
]
WALL_AREA_COLUMNS = [
    "room_id",
    "room_name",
    "wall_id",
    "wall_ref",
    "section_id",
    "wall_length_m",
    "wall_height_m",
    "height_source",
    "opening_ids",
    "gross_wall_area_m2",
    "opening_area_m2",
    "net_wall_area_m2",
    "review_status",
    "operator_note",
    "export_ready",
]
SCHEMAS = {
    "aufmass_section_alignment.schema.json": (
        "skeleton.aufmass_section_alignment.schema.json",
        SECTION_ALIGNMENT_COLUMNS,
        "alignment_status_counts",
        "section_alignment_review_table",
    ),
    "aufmass_opening_height_review.schema.json": (
        "skeleton.aufmass_opening_height_review.schema.json",
        OPENING_HEIGHT_COLUMNS,
        "review_status_counts",
        "opening_height_review_table",
    ),
    "aufmass_wall_area_review.schema.json": (
        "skeleton.aufmass_wall_area_review.schema.json",
        WALL_AREA_COLUMNS,
        "review_status_counts",
        "wall_area_review_table",
    ),
}
ALIGNMENT_STATUSES = ["candidate", "needs_review", "aligned", "conflict"]


def load_schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_review_table_schemas_load_and_pin_fixed_columns() -> None:
    for name, (schema_id, columns, status_count_field, stage) in SCHEMAS.items():
        schema = load_schema(name)
        defs = schema["$defs"]  # type: ignore[index]
        row = defs["row"]
        summary = defs["summary"]

        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == schema_id
        assert schema["required"] == ["source_units", "columns", "rows", "summary"]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["columns"]["const"] == columns
        assert row["required"] == columns
        assert set(row["properties"]) == set(columns)
        assert row["additionalProperties"] is False
        assert row["properties"]["export_ready"]["const"] is False
        assert status_count_field in summary["required"]
        assert summary["properties"]["stage"]["const"] == stage
        assert summary["properties"]["official_quantities"]["const"] is False


def test_opening_height_schema_reuses_section_alignment_statuses() -> None:
    section = load_schema("aufmass_section_alignment.schema.json")
    opening = load_schema("aufmass_opening_height_review.schema.json")
    section_row = section["$defs"]["row"]  # type: ignore[index]
    opening_row = opening["$defs"]["row"]  # type: ignore[index]

    assert section_row["properties"]["alignment_status"]["enum"] == ALIGNMENT_STATUSES
    assert opening_row["properties"]["section_alignment_status"]["enum"] == ALIGNMENT_STATUSES


def test_wall_area_schema_keeps_review_values_nullable_and_traced_to_openings() -> None:
    schema = load_schema("aufmass_wall_area_review.schema.json")
    row = schema["$defs"]["row"]  # type: ignore[index]
    optional_number = schema["$defs"]["optional_non_negative_number"]  # type: ignore[index]

    assert optional_number == {"type": ["number", "null"], "minimum": 0}
    assert row["properties"]["opening_ids"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    for column in (
        "wall_length_m",
        "wall_height_m",
        "gross_wall_area_m2",
        "opening_area_m2",
        "net_wall_area_m2",
    ):
        assert row["properties"][column]["$ref"] == "#/$defs/optional_non_negative_number"


def test_route_doc_lists_all_review_columns_and_review_only_gates() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    for columns in (SECTION_ALIGNMENT_COLUMNS, OPENING_HEIGHT_COLUMNS, WALL_AREA_COLUMNS):
        for column in columns:
            assert f"`{column}`" in doc

    assert "`export_ready: false`" in doc
    assert "`official_quantities: false`" in doc
    assert "It does not add a section matcher" in doc
