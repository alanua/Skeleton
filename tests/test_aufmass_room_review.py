from __future__ import annotations

import builtins
import json
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.aufmass_room_matcher import match_dxf_rooms, room_match_result_to_dict
from core.aufmass_room_review import (
    ROOM_REVIEW_COLUMNS,
    room_matches_to_review_table,
    room_review_table_to_dict,
    room_review_table_to_rows,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "aufmass_room_review.schema.json"


def test_room_match_candidates_convert_to_deterministic_review_rows() -> None:
    table = room_matches_to_review_table(match_dxf_rooms(_fixture()))

    rows = room_review_table_to_rows(table)

    assert rows[0] == {
        "room_id": "match-1",
        "room_name": "Office 101",
        "source_ref": "match-1",
        "source_layer": "ROOMS",
        "contour_id": "contour-1",
        "contour_status": "candidate",
        "label_text": "Office 101",
        "label_status": "linked",
        "area_from_label_m2": 12.0,
        "area_calculated_m2": 12.0,
        "area_delta_m2": 0.0,
        "area_delta_percent": 0.0,
        "height_m": None,
        "height_source": "",
        "openings_status": "not_reviewed",
        "review_status": "candidate",
        "operator_note": "",
        "export_ready": False,
    }
    assert [list(row) for row in rows] == [ROOM_REVIEW_COLUMNS, ROOM_REVIEW_COLUMNS]
    assert table.summary == {
        "row_count": 2,
        "review_status_counts": {"area_mismatch": 1, "candidate": 1},
        "stage": "room_review_table",
        "official_quantities": False,
    }


def test_room_review_rows_preserve_mismatch_and_missing_label_review_context() -> None:
    fixture = _fixture()
    fixture["texts"] = []
    fixture["mtexts"][1]["text"] = "NGF 3.00 m2"

    rows = room_review_table_to_rows(room_matches_to_review_table(match_dxf_rooms(fixture)))

    mismatch_row = rows[1]
    assert mismatch_row["review_status"] == "area_mismatch"
    assert mismatch_row["label_status"] == "missing"
    assert mismatch_row["room_name"] == ""
    assert mismatch_row["area_from_label_m2"] == pytest.approx(3.0)
    assert mismatch_row["area_calculated_m2"] == pytest.approx(4.0)
    assert mismatch_row["area_delta_m2"] == pytest.approx(1.0)
    assert mismatch_row["area_delta_percent"] == pytest.approx(100.0 / 3.0)
    assert "No non-area room label" in mismatch_row["operator_note"]
    assert "Calculated polyline area differs" in mismatch_row["operator_note"]
    assert mismatch_row["export_ready"] is False


def test_room_review_dict_is_json_compatible_and_declares_fixed_columns() -> None:
    table = room_matches_to_review_table(match_dxf_rooms(_fixture()))

    payload = room_review_table_to_dict(table)

    assert payload["source_units"] == "m"
    assert payload["columns"] == ROOM_REVIEW_COLUMNS
    assert payload["summary"]["stage"] == "room_review_table"  # type: ignore[index]
    json.dumps(payload)


def test_room_review_table_accepts_only_room_match_result() -> None:
    with pytest.raises(TypeError, match="RoomMatchResult"):
        room_matches_to_review_table(room_match_result_to_dict(match_dxf_rooms(_fixture())))  # type: ignore[arg-type]


def test_schema_documents_review_table_shape() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.aufmass_room_review.schema.json"
    assert schema["required"] == ["source_units", "columns", "rows", "summary"]
    assert schema["properties"]["columns"]["const"] == ROOM_REVIEW_COLUMNS
    assert set(schema["$defs"]["row"]["properties"]) == set(ROOM_REVIEW_COLUMNS)
    assert schema["$defs"]["row"]["properties"]["export_ready"]["const"] is False


def test_room_review_core_functions_have_no_file_network_or_subprocess_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_mock = Mock(side_effect=AssertionError("open must not be called"))
    urlopen_mock = Mock(side_effect=AssertionError("urlopen must not be called"))
    run_mock = Mock(side_effect=AssertionError("subprocess.run must not be called"))
    popen_mock = Mock(side_effect=AssertionError("subprocess.Popen must not be called"))
    monkeypatch.setattr(builtins, "open", open_mock)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen_mock)
    monkeypatch.setattr(subprocess, "run", run_mock)
    monkeypatch.setattr(subprocess, "Popen", popen_mock)

    table = room_matches_to_review_table(match_dxf_rooms(_fixture()))
    room_review_table_to_rows(table)
    room_review_table_to_dict(table)

    open_mock.assert_not_called()
    urlopen_mock.assert_not_called()
    run_mock.assert_not_called()
    popen_mock.assert_not_called()


def _fixture() -> dict[str, object]:
    return {
        "units": "m",
        "insunits": 6,
        "polylines": [
            {
                "layer": "ROOMS",
                "entity_type": "LWPOLYLINE",
                "closed": True,
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 4, "y": 0},
                    {"x": 4, "y": 3},
                    {"x": 0, "y": 3},
                ],
            },
            {
                "layer": "ROOMS",
                "entity_type": "LWPOLYLINE",
                "closed": True,
                "points": [
                    {"x": 10, "y": 0},
                    {"x": 12, "y": 0},
                    {"x": 12, "y": 2},
                    {"x": 10, "y": 2},
                ],
            },
        ],
        "texts": [
            {"layer": "ROOM_NAMES", "text": "Office 101", "insert": {"x": 1, "y": 1}},
            {"layer": "ROOM_NAMES", "text": "Storage", "insert": {"x": 11, "y": 1}},
        ],
        "mtexts": [
            {"layer": "AREAS", "text": "NGF: 12.00 m2", "insert": {"x": 3, "y": 1}},
            {"layer": "AREAS", "text": "NGF 3.00 m2", "insert": {"x": 11.5, "y": 1}},
        ],
    }
