from __future__ import annotations

import json
from pathlib import Path

from core.aufmass_source_pack import (
    MANIFEST_SCHEMA,
    source_pack_manifest_from_dict,
    validate_source_pack_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "aufmass_source_pack.schema.json"
DOC_PATH = ROOT / "docs" / "AUFMASS_SOURCE_PACK.md"


def manifest(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "pack_id": "synthetic-pack-1",
        "project_id": "aufmass",
        "sources": [
            {
                "source_id": "synthetic-dxf-1",
                "source_type": "dxf",
                "artifact_ref": "synthetic-plan-a",
                "artifact_route": "public_synthetic",
                "metadata": {
                    "title": "Synthetic calibration plan",
                    "source_revision": "rev-a",
                    "prepared_by": "synthetic-test",
                },
                "scale_hint": {
                    "basis": "known_dimension",
                    "detail": "synthetic 5m calibration segment",
                },
                "privacy_status": "synthetic",
                "review_status": "approved_for_private_intake",
            }
        ],
    }
    values.update(overrides)
    return values


def test_valid_synthetic_manifest_passes_without_warnings() -> None:
    result = validate_source_pack_manifest(manifest())

    assert result.ok is True
    assert result.errors == []
    assert result.warnings == []


def test_manifest_dataclass_model_builds_after_validation() -> None:
    model = source_pack_manifest_from_dict(manifest())

    assert model.schema == MANIFEST_SCHEMA
    assert model.pack_id == "synthetic-pack-1"
    assert model.sources[0].source_type == "dxf"
    assert model.sources[0].scale_hint.basis == "known_dimension"


def test_missing_required_metadata_is_an_error() -> None:
    data = manifest()
    source = data["sources"][0]  # type: ignore[index]
    del source["metadata"]["prepared_by"]  # type: ignore[index]

    result = validate_source_pack_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.sources[0].metadata.prepared_by", "missing_required")
    ]


def test_unsupported_source_type_is_an_error() -> None:
    data = manifest(
        sources=[
            {
                "source_id": "synthetic-unknown-1",
                "source_type": "spreadsheet",
                "artifact_ref": "synthetic-table-a",
                "artifact_route": "public_synthetic",
                "metadata": {
                    "title": "Synthetic table",
                    "source_revision": "rev-a",
                    "prepared_by": "synthetic-test",
                },
                "scale_hint": {"basis": "not_applicable"},
                "privacy_status": "synthetic",
                "review_status": "reviewed",
            }
        ]
    )

    result = validate_source_pack_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.sources[0].source_type", "unsupported_source_type")
    ]


def test_geometric_source_requires_scale_hint() -> None:
    data = manifest()
    source = data["sources"][0]  # type: ignore[index]
    source["scale_hint"] = {"basis": "not_applicable"}  # type: ignore[index]

    result = validate_source_pack_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.sources[0].scale_hint.basis", "scale_required")
    ]


def test_unknown_geometric_scale_is_deterministic_warning() -> None:
    data = manifest()
    source = data["sources"][0]  # type: ignore[index]
    source["scale_hint"] = {"basis": "unknown"}  # type: ignore[index]
    source["review_status"] = "needs_review"  # type: ignore[index]

    result = validate_source_pack_manifest(data)

    assert result.ok is True
    assert [(issue.path, issue.code) for issue in result.warnings] == [
        ("$.sources[0].scale_hint.basis", "scale_needs_review"),
        ("$.sources[0].review_status", "review_not_final"),
    ]


def test_private_pilot_source_must_use_private_route() -> None:
    data = manifest()
    source = data["sources"][0]  # type: ignore[index]
    source["privacy_status"] = "private_pilot"  # type: ignore[index]

    result = validate_source_pack_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.sources[0].artifact_route", "private_route_required")
    ]


def test_public_safe_source_must_not_use_private_route() -> None:
    data = manifest()
    source = data["sources"][0]  # type: ignore[index]
    source["privacy_status"] = "public_safe"  # type: ignore[index]
    source["artifact_route"] = "private_drive"  # type: ignore[index]

    result = validate_source_pack_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.sources[0].artifact_route", "public_route_required")
    ]


def test_artifact_ref_rejects_paths_urls_and_private_route_details() -> None:
    data = manifest()
    source = data["sources"][0]  # type: ignore[index]
    source["artifact_ref"] = "https://drive.google.example/private/file"  # type: ignore[index]

    result = validate_source_pack_manifest(data)

    assert result.ok is False
    assert [issue.code for issue in result.errors] == [
        "invalid_artifact_ref",
        "private_reference_leak",
        "private_reference_leak",
    ]


def test_duplicate_source_ids_are_errors() -> None:
    first = manifest()["sources"][0]  # type: ignore[index]
    second = dict(first)
    data = manifest(sources=[first, second])

    result = validate_source_pack_manifest(data)

    assert result.ok is False
    assert [(issue.path, issue.code) for issue in result.errors] == [
        ("$.sources[1].source_id", "duplicate_source_id")
    ]


def test_schema_loads_and_matches_validator_enums() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.aufmass_source_pack.schema.json"
    assert schema["properties"]["schema"]["const"] == MANIFEST_SCHEMA
    assert "sources" in schema["required"]
    assert schema["$defs"]["source"]["additionalProperties"] is False


def test_docs_state_private_boundary_and_limits() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "Real source files stay private." in doc
    assert "no OCR" in doc
    assert "no PDF, DXF, IFC, or image parser changes" in doc
