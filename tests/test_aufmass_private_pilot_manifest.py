from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from core.aufmass_private_pilot_manifest import (
    MANIFEST_SCHEMA,
    private_pilot_manifest_from_dict,
    validate_private_pilot_manifest,
)


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


def manifest(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "pilot_id": "private-pilot-synthetic-1",
        "project_id": "aufmass",
        "route_stage": "room_review_table",
        "private_refs": [
            {
                "private_ref": "private-ref-synthetic-1",
                "source_type": "manual_room_list",
                "review_stage": "room_review_table",
                "artifact_kind": "room_review_table",
                "artifact_route": "private_operator_handoff",
                "review_status": "export_ready",
                "public_safety_status": "private_only",
            }
        ],
        "public_safety": {"status": "private_only"},
        "notes": ["synthetic manifest metadata only"],
    }
    values.update(overrides)
    return deepcopy(values)


def load_schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_valid_minimal_private_pilot_manifest() -> None:
    result = validate_private_pilot_manifest(manifest())

    assert result.ok is True
    assert result.errors == []
    assert result.warnings == []


def test_manifest_dataclass_model_builds_after_validation() -> None:
    model = private_pilot_manifest_from_dict(manifest())

    assert model.schema == MANIFEST_SCHEMA
    assert model.pilot_id == "private-pilot-synthetic-1"
    assert model.project_id == "aufmass"
    assert model.private_refs[0].private_ref == "private-ref-synthetic-1"
    assert model.public_safety.status == "private_only"


def test_pilot_id_and_project_id_are_required() -> None:
    data = manifest()
    del data["pilot_id"]
    del data["project_id"]

    result = validate_private_pilot_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.pilot_id", "missing_required"),
        ("$.project_id", "missing_required"),
        ("$.project_id", "invalid_project_id"),
    ]


def test_reject_path_like_artifact_refs() -> None:
    data = manifest()
    private_ref = data["private_refs"][0]  # type: ignore[index]
    private_ref["private_ref"] = "private-ref-folder/file"  # type: ignore[index]

    result = validate_private_pilot_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.private_refs[0].private_ref", "invalid_private_ref"),
        ("$.private_refs[0].private_ref", "private_reference_leak"),
    ]


def test_reject_url_like_artifact_refs() -> None:
    data = manifest()
    private_ref = data["private_refs"][0]  # type: ignore[index]
    private_ref["private_ref"] = "https://redacted.invalid/private-ref-synthetic-1"  # type: ignore[index]

    result = validate_private_pilot_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.private_refs[0].private_ref", "invalid_private_ref"),
        ("$.private_refs[0].private_ref", "private_reference_leak"),
        ("$.private_refs[0].private_ref", "private_reference_leak"),
    ]


def test_reject_public_route_for_private_pilot_artifacts() -> None:
    data = manifest(route_stage="public_safe_lessons")
    private_ref = data["private_refs"][0]  # type: ignore[index]
    private_ref["review_stage"] = "public_safe_lessons"  # type: ignore[index]
    private_ref["artifact_route"] = "public_synthetic"  # type: ignore[index]

    result = validate_private_pilot_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.route_stage", "public_route_for_private_artifacts"),
        ("$.private_refs[0].review_stage", "public_route_for_private_artifacts"),
        ("$.private_refs[0].artifact_route", "unsupported_private_route"),
    ]


def test_output_and_report_refs_must_be_opaque_or_private_route_only() -> None:
    data = manifest(
        output_refs=[{"ref": "private-ref-output-1", "route": "private_local_runner"}],
        report_refs=[{"ref": "private-ref-report/1", "route": "public_synthetic"}],
    )

    result = validate_private_pilot_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.report_refs[0].ref", "invalid_private_ref"),
        ("$.report_refs[0].ref", "private_reference_leak"),
        ("$.report_refs[0].route", "unsupported_private_route"),
    ]


def test_deterministic_errors_and_warnings() -> None:
    data = manifest(schema="wrong.schema", route_stage="unknown_stage")
    private_ref = data["private_refs"][0]  # type: ignore[index]
    private_ref["review_status"] = "needs_review"  # type: ignore[index]
    private_ref["source_type"] = "spreadsheet"  # type: ignore[index]
    private_ref["artifact_route"] = "private_local_runner"  # type: ignore[index]

    result = validate_private_pilot_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code, issue.message) for issue in result.errors] == [
        ("$.schema", "invalid_schema", "schema must be skeleton.aufmass_private_pilot_manifest.v1."),
        ("$.route_stage", "unsupported_route_stage", "route_stage is not approved."),
        ("$.private_refs[0].source_type", "unsupported_source_type", "source_type is not approved."),
    ]
    assert [(issue.path, issue.code, issue.message) for issue in result.warnings] == [
        ("$.private_refs[0].review_status", "review_not_final", "private artifact review is not final.")
    ]


def test_schema_loads_as_json() -> None:
    schema = load_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "skeleton.aufmass_private_pilot_manifest.schema.json"


def test_schema_requires_public_safety() -> None:
    schema = load_schema()

    assert "public_safety" in schema["required"]  # type: ignore[operator]
    assert "pilot_id" in schema["required"]  # type: ignore[operator]


def test_schema_disallows_additional_properties() -> None:
    schema = load_schema()
    defs = schema["$defs"]  # type: ignore[index]

    assert schema["additionalProperties"] is False
    assert defs["private_ref"]["additionalProperties"] is False
    assert defs["routed_private_ref"]["additionalProperties"] is False
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
