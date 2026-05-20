from __future__ import annotations

import builtins
import json
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.aufmass_engine import AufmassInput, Opening, Point, RoomInput, calculate_aufmass
from core.aufmass_exporter import (
    EXPORT_COLUMNS,
    aufmass_result_to_csv,
    aufmass_result_to_json_dict,
    aufmass_result_to_rows,
    aufmass_result_to_summary_row,
)
from core.aufmass_manual_adapter import (
    ManualAufmassInput,
    ManualOpeningInput,
    ManualPoint,
    ManualRoomInput,
    ScaleCalibration,
    calculate_manual_plan_aufmass,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "aufmass_export.schema.json"


def rectangle_room(**overrides: object) -> RoomInput:
    values = {
        "room_id": "room-1",
        "name": "Export Test Room",
        "height": 2.5,
        "polygon": [
            Point(0, 0),
            Point(4, 0),
            Point(4, 3),
            Point(0, 3),
        ],
        "openings": [
            Opening(width=0.9, height=2.0, count=1),
            Opening(width=1.2, height=1.0, count=2),
        ],
    }
    values.update(overrides)
    return RoomInput(**values)


def aufmass_result(*rooms: RoomInput):
    return calculate_aufmass(AufmassInput(project_id="public-export-test", unit="m", rooms=list(rooms)))


def manual_input() -> ManualAufmassInput:
    return ManualAufmassInput(
        project_id="public-manual-export-test",
        calibration=ScaleCalibration(
            point_a=ManualPoint(10, 20),
            point_b=ManualPoint(110, 20),
            real_length_m=5.0,
        ),
        rooms=[
            ManualRoomInput(
                room_id="manual-room-1",
                name="Manual Export Room",
                height_m=2.5,
                polygon=[
                    ManualPoint(10, 20),
                    ManualPoint(90, 20),
                    ManualPoint(90, 80),
                    ManualPoint(10, 80),
                ],
                openings=[
                    ManualOpeningInput(width=18, height=40, count=1),
                    ManualOpeningInput(width=1.2, height=1.0, count=2, dimension_unit="m"),
                ],
            )
        ],
    )


def test_room_rows_generated_from_simple_result() -> None:
    rows = aufmass_result_to_rows(aufmass_result(rectangle_room()))

    assert rows[0] == {
        "row_type": "room",
        "project_id": "public-export-test",
        "unit": "m",
        "room_id": "room-1",
        "room_name": "Export Test Room",
        "floor_area": 12.0,
        "ceiling_area": 12.0,
        "perimeter": 14.0,
        "gross_wall_area": 35.0,
        "openings_area": 4.2,
        "net_wall_area": 30.8,
        "volume": 30.0,
    }


def test_summary_row_generated_correctly() -> None:
    summary = aufmass_result_to_summary_row(aufmass_result(rectangle_room()))

    assert summary == {
        "row_type": "summary",
        "project_id": "public-export-test",
        "unit": "m",
        "room_id": "",
        "room_name": "Summary",
        "floor_area": 12.0,
        "ceiling_area": 12.0,
        "perimeter": 14.0,
        "gross_wall_area": 35.0,
        "openings_area": 4.2,
        "net_wall_area": 30.8,
        "volume": 30.0,
    }


def test_csv_contains_deterministic_header_room_rows_and_summary_row() -> None:
    csv_text = aufmass_result_to_csv(aufmass_result(rectangle_room()))

    assert csv_text == (
        "row_type,project_id,unit,room_id,room_name,floor_area,ceiling_area,perimeter,"
        "gross_wall_area,openings_area,net_wall_area,volume\n"
        "room,public-export-test,m,room-1,Export Test Room,12.000,12.000,14.000,"
        "35.000,4.200,30.800,30.000\n"
        "summary,public-export-test,m,,Summary,12.000,12.000,14.000,35.000,4.200,"
        "30.800,30.000\n"
    )


def test_json_dict_contains_project_unit_columns_rooms_and_summary() -> None:
    result = aufmass_result(rectangle_room())

    exported = aufmass_result_to_json_dict(result)

    assert exported["project_id"] == "public-export-test"
    assert exported["unit"] == "m"
    assert exported["columns"] == EXPORT_COLUMNS
    assert exported["rooms"] == [
        {
            "row_type": "room",
            "project_id": "public-export-test",
            "unit": "m",
            "room_id": "room-1",
            "room_name": "Export Test Room",
            "floor_area": 12.0,
            "ceiling_area": 12.0,
            "perimeter": 14.0,
            "gross_wall_area": 35.0,
            "openings_area": pytest.approx(4.2),
            "net_wall_area": pytest.approx(30.8),
            "volume": 30.0,
        }
    ]
    assert exported["summary"] == {
        "row_type": "summary",
        "project_id": "public-export-test",
        "unit": "m",
        "room_count": 1,
        "room_id": "",
        "room_name": "Summary",
        "floor_area": 12.0,
        "ceiling_area": 12.0,
        "perimeter": 14.0,
        "gross_wall_area": 35.0,
        "openings_area": pytest.approx(4.2),
        "net_wall_area": pytest.approx(30.8),
        "volume": 30.0,
    }
    json.dumps(exported)


def test_multiple_room_result_exports_in_engine_order() -> None:
    result = aufmass_result(
        rectangle_room(room_id="room-b", name="Second"),
        rectangle_room(
            room_id="room-a",
            name="First",
            height=3.0,
            polygon=[
                Point(0, 0),
                Point(2, 0),
                Point(2, 2),
                Point(0, 2),
            ],
            openings=[],
        ),
    )

    rows = aufmass_result_to_rows(result)
    csv_lines = aufmass_result_to_csv(result).splitlines()

    assert [row["room_id"] for row in rows] == ["room-b", "room-a", ""]
    assert csv_lines[1].startswith("room,public-export-test,m,room-b,Second,")
    assert csv_lines[2].startswith("room,public-export-test,m,room-a,First,")
    assert csv_lines[3].startswith("summary,public-export-test,m,,Summary,")


def test_manual_adapter_engine_exporter_integration_returns_expected_csv_and_json_values() -> None:
    result = calculate_manual_plan_aufmass(manual_input())

    csv_text = aufmass_result_to_csv(result)
    exported = aufmass_result_to_json_dict(result)

    assert "room,public-manual-export-test,m,manual-room-1,Manual Export Room,12.000" in csv_text
    assert "35.000,4.200,30.800,30.000" in csv_text
    assert exported["project_id"] == "public-manual-export-test"
    assert exported["unit"] == "m"
    assert exported["rooms"][0]["floor_area"] == pytest.approx(12.0)
    assert exported["rooms"][0]["net_wall_area"] == pytest.approx(30.8)
    assert exported["summary"]["volume"] == pytest.approx(30.0)


def test_unsupported_result_object_blocked() -> None:
    with pytest.raises(TypeError, match="result must be an AufmassResult"):
        aufmass_result_to_csv("private/path.pdf")  # type: ignore[arg-type]


def test_schema_file_exists_and_documents_export_shape() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.aufmass_export.schema.json"
    assert schema["required"] == ["project_id", "unit", "columns", "rooms", "summary"]
    assert schema["properties"]["columns"]["const"] == EXPORT_COLUMNS
    assert schema["properties"]["rooms"]["items"] == {"$ref": "#/$defs/room_row"}
    assert schema["properties"]["summary"] == {"$ref": "#/$defs/summary"}
    assert set(schema["$defs"]["room_row"]["properties"]) >= set(EXPORT_COLUMNS)
    assert set(schema["$defs"]["summary"]["properties"]) >= {
        "row_type",
        "room_count",
        "floor_area",
        "net_wall_area",
        "volume",
    }


def test_exporter_functions_have_no_file_network_subprocess_side_effects(
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

    result = aufmass_result(rectangle_room())

    aufmass_result_to_rows(result)
    aufmass_result_to_summary_row(result)
    aufmass_result_to_csv(result)
    aufmass_result_to_json_dict(result)

    open_mock.assert_not_called()
    urlopen_mock.assert_not_called()
    run_mock.assert_not_called()
    popen_mock.assert_not_called()
