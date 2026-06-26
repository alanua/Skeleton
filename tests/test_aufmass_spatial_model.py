from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from jsonschema import validate

from core import aufmass_spatial_model as spatial_module
from core.aufmass_spatial_model import build_aufmass_foundation


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "aufmass_foundation.schema.json").read_text(encoding="utf-8"))


def test_three_coloured_zones_remain_one_physical_room(monkeypatch) -> None:
    _patch_geometry(monkeypatch)

    result = build_aufmass_foundation(_plan())

    model = result["room_and_zone_model"]
    assert len(model["physical_rooms"]) == 4
    selected = next(room for room in model["physical_rooms"] if room["room_id"] == model["selected_room_id"])
    assert len(selected["zone_ids"]) == 3
    assert model["label_or_color_splits_room"] is False
    assert result["qa"]["functional_zone_count"] == 3
    assert len(result["apartment_graph"]["boundary"]) == 4
    assert result["apartment_graph"]["entrance_ids"] == ["entrance-001"]


def test_two_enclosed_rooms_remain_distinct_even_when_zone_evidence_is_close(monkeypatch) -> None:
    _patch_geometry(monkeypatch)
    plan = _plan()
    plan["physical_rooms"] = plan["physical_rooms"][:2]
    plan["connections"] = [{"from": "open-space", "to": "service-space"}]

    result = build_aufmass_foundation(plan)

    assert result["qa"]["physical_room_count"] == 2
    connected = [edge for edge in result["apartment_graph"]["edges"] if edge["relation"] == "CONNECTED_TO"]
    assert len(connected) == 1


def test_selected_room_only_dxf_crosscheck_never_enumerates_whole_file(monkeypatch) -> None:
    _patch_geometry(monkeypatch)
    dxf = {
        "selected_room_id": "open-space",
        "alignment_status": "matched",
        "anchors": [{"id": "anchor-a"}, {"id": "anchor-b"}],
        "segment_mappings": [{"plan": "a", "dxf": "x"}],
    }

    result = build_aufmass_foundation(_plan(), dxf_evidence=dxf)

    assert result["status"] == "dxf_crosschecked"
    assert result["dxf_crosscheck"]["source_scope"] == "selected_room_only"
    assert result["dxf_crosscheck"]["anchor_count"] == 2
    assert result["qa"]["whole_dxf_enumeration_used"] is False


def test_whole_file_dxf_payload_fails_closed_without_geometry_run(monkeypatch) -> None:
    def forbidden_geometry(*args, **kwargs):
        raise AssertionError("geometry must not run for a rejected whole-file DXF payload")

    monkeypatch.setattr(spatial_module, "build_room_geometry_evidence", forbidden_geometry)
    result = build_aufmass_foundation(
        _plan(),
        dxf_evidence={
            "selected_room_id": "open-space",
            "polylines": [],
            "texts": [],
        },
    )

    assert result["status"] == "failed_safe"
    assert result["qa"]["blockers"] == ["whole_file_dxf_payload_rejected"]


def test_material_pdf_dxf_conflict_is_not_quantity_ready(monkeypatch) -> None:
    _patch_geometry(monkeypatch)

    result = build_aufmass_foundation(
        _plan(),
        dxf_evidence={
            "selected_room_id": "open-space",
            "alignment_status": "matched",
            "material_conflict": True,
        },
    )

    assert result["status"] == "material_source_conflict"
    assert result["qa"]["quantity_ready"] is False


def test_missing_calibration_returns_needs_scale() -> None:
    plan = _plan()
    plan["calibration"] = {"status": "unknown", "evidence": []}

    result = build_aufmass_foundation(plan)

    assert result["status"] == "needs_scale"
    assert result["room_geometry_candidate"] is None


def test_pdf_image_geometry_is_primary_immutable_and_unrelated_geometry_is_ignored(monkeypatch) -> None:
    _patch_geometry(monkeypatch)
    plan = _plan()
    plan["unrelated_geometry"] = [{"private": "must-not-enter-output"}]
    original = deepcopy(plan)

    result = build_aufmass_foundation(plan)

    assert plan == original
    assert result["foundation_provenance"]["primary_source"] == "calibrated_pdf_image"
    assert result["foundation_provenance"]["source_geometry_immutable"] is True
    assert result["qa"]["unrelated_geometry_ignored"] is True
    assert "must-not-enter-output" not in json.dumps(result)


def test_output_is_deterministic_schema_valid_and_contains_no_private_source_fields(monkeypatch) -> None:
    _patch_geometry(monkeypatch)
    plan = _plan()
    plan["address"] = "private-address"
    plan["file_name"] = "private-plan.pdf"
    plan["apartment_number"] = "private-unit"
    plan["physical_rooms"][0]["label"] = "private-room-name"

    first = build_aufmass_foundation(plan)
    second = build_aufmass_foundation(plan)

    assert first == second
    validate(first, SCHEMA)
    serialized = json.dumps(first, sort_keys=True)
    for forbidden in ("private-address", "private-plan.pdf", "private-unit", "private-room-name"):
        assert forbidden not in serialized


def _patch_geometry(monkeypatch) -> None:
    def fake_geometry(room, *, tolerance_mm):
        return {
            "contract": "ROOM_GEOMETRY_EVIDENCE",
            "status": "fast_estimate_accepted",
            "geometry_source": "calibrated_pdf_image",
            "source_geometry": {"immutable": True, "segments": deepcopy(room["boundary_segments"])},
            "room_geometry_candidate": {
                "contract": "ROOM_GEOMETRY_CANDIDATE",
                "room_id": room["room_id"],
                "status": "fast_estimate_accepted",
                "area_m2": 12.0,
                "area_report_m2": 12.0,
                "perimeter_m": 14.0,
                "accepted_room_shell": {"contract": "ACCEPTED_ROOM_SHELL"},
                "failure_reason": None,
            },
            "repair_evidence": [],
            "engine": {"name": "core2d", "manifest_hash": "synthetic-hash"},
            "qa": {"selected_room_only": True, "source_unchanged": True},
            "evidence_hash": "synthetic-evidence-hash",
        }

    monkeypatch.setattr(spatial_module, "build_room_geometry_evidence", fake_geometry)


def _plan():
    return {
        "source_kind": "operator_annotated_image",
        "building_part_profile": "SIDE_WING",
        "calibration": {"status": "confirmed", "evidence": ["printed_dimension"]},
        "selected_room_id": "open-space",
        "apartment_boundary": [[0.0, 0.0], [12.0, 0.0], [12.0, 6.0], [0.0, 6.0]],
        "entrances": [{"connected_room_id": "open-space"}],
        "physical_rooms": [
            {
                "room_id": "open-space",
                "room_type": "open_physical_room",
                "boundary_segments": [
                    {"id": "s1", "start": [0.0, 0.0], "end": [6.0, 0.0]},
                    {"id": "s2", "start": [6.0, 0.0], "end": [6.0, 4.0]},
                    {"id": "s3", "start": [6.0, 4.0], "end": [0.0, 4.0]},
                    {"id": "s4", "start": [0.0, 4.0], "end": [0.0, 0.0]},
                ],
                "zones": [
                    {"zone_id": "source-circulation", "role": "circulation_zone", "color_token": "c1"},
                    {"zone_id": "source-cooking", "role": "cooking_zone", "color_token": "c2"},
                    {"zone_id": "source-living", "role": "living_zone", "color_token": "c3"},
                ],
            },
            {"room_id": "service-space", "room_type": "service_room", "zones": []},
            {"room_id": "room-b", "room_type": "physical_room", "zones": []},
            {"room_id": "room-c", "room_type": "physical_room", "zones": []},
        ],
        "connections": [
            {"from": "open-space", "to": "service-space"},
            {"from": "open-space", "to": "room-b"},
            {"from": "open-space", "to": "room-c"},
        ],
    }
