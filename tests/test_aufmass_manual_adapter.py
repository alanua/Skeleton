from __future__ import annotations

import builtins
import json
import subprocess
import urllib.request
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.aufmass_manual_adapter import (
    ManualAufmassInput,
    ManualOpeningInput,
    ManualPoint,
    ManualRoomInput,
    ScaleCalibration,
    calculate_manual_plan_aufmass,
    convert_manual_plan,
    convert_manual_plan_to_aufmass,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "aufmass_manual_input.schema.json"


def manual_input(**overrides: object) -> ManualAufmassInput:
    values = {
        "project_id": "public-manual-test",
        "calibration": ScaleCalibration(
            point_a=ManualPoint(10, 20),
            point_b=ManualPoint(110, 20),
            real_length_m=5.0,
            source_page="1",
            source_ref="scale-line",
            confidence=0.9,
            review_status="reviewed",
        ),
        "rooms": [
            ManualRoomInput(
                room_id="room-1",
                name="Manual Test Room",
                height_m=2.5,
                polygon=[
                    ManualPoint(10, 20),
                    ManualPoint(90, 20),
                    ManualPoint(90, 80),
                    ManualPoint(10, 80),
                ],
                source_page="1",
                source_ref="room-outline",
                confidence=0.8,
                review_status="needs_review",
            )
        ],
        "source_page": "1",
        "source_ref": "manual-public-example",
        "confidence": 0.75,
        "review_status": "needs_review",
    }
    values.update(overrides)
    return ManualAufmassInput(**values)


def test_calibration_scale_calculation_from_two_points_and_known_real_length() -> None:
    result = convert_manual_plan(manual_input())

    assert result.audit.calibration_distance_units == pytest.approx(100.0)
    assert result.audit.scale_m_per_unit == pytest.approx(0.05)
    assert result.audit.origin.x == pytest.approx(10.0)
    assert result.audit.origin.y == pytest.approx(20.0)


def test_rectangle_room_manual_coordinates_converted_to_meters_and_calculated() -> None:
    aufmass_input = convert_manual_plan_to_aufmass(manual_input())

    room = aufmass_input.rooms[0]
    assert aufmass_input.unit == "m"
    assert [(point.x, point.y) for point in room.polygon] == pytest.approx(
        [(0, 0), (4, 0), (4, 3), (0, 3)]
    )

    result = calculate_manual_plan_aufmass(manual_input()).rooms[0]
    assert result.floor_area == pytest.approx(12.0)
    assert result.perimeter == pytest.approx(14.0)
    assert result.volume == pytest.approx(30.0)


def test_offset_origin_handling_preserves_area_and_perimeter() -> None:
    input_data = manual_input(
        calibration=ScaleCalibration(
            point_a=ManualPoint(500, 600),
            point_b=ManualPoint(600, 600),
            real_length_m=10.0,
        ),
        rooms=[
            ManualRoomInput(
                room_id="offset-room",
                height_m=3.0,
                polygon=[
                    ManualPoint(540, 620),
                    ManualPoint(580, 620),
                    ManualPoint(580, 650),
                    ManualPoint(540, 650),
                ],
            )
        ],
    )

    result = calculate_manual_plan_aufmass(input_data).rooms[0]

    assert result.floor_area == pytest.approx(12.0)
    assert result.perimeter == pytest.approx(14.0)


def test_opening_dimensions_converted_from_drawing_units_to_meters() -> None:
    input_data = manual_input(
        rooms=[
            ManualRoomInput(
                room_id="room-1",
                height_m=2.5,
                polygon=[
                    ManualPoint(10, 20),
                    ManualPoint(90, 20),
                    ManualPoint(90, 80),
                    ManualPoint(10, 80),
                ],
                openings=[
                    ManualOpeningInput(
                        opening_id="door-1",
                        name="Door",
                        width=18,
                        height=40,
                        count=1,
                        source_page="1",
                        source_ref="door-mark",
                        confidence=0.7,
                        review_status="reviewed",
                    )
                ],
            )
        ]
    )

    room = convert_manual_plan_to_aufmass(input_data).rooms[0]

    assert room.openings[0].width == pytest.approx(0.9)
    assert room.openings[0].height == pytest.approx(2.0)
    assert room.openings[0].source == "page=1; ref=door-mark; review=reviewed"
    assert room.openings[0].confidence == pytest.approx(0.7)


def test_opening_dimensions_already_metric_when_explicitly_marked_metric() -> None:
    input_data = manual_input(
        rooms=[
            ManualRoomInput(
                room_id="room-1",
                height_m=2.5,
                polygon=[
                    ManualPoint(10, 20),
                    ManualPoint(90, 20),
                    ManualPoint(90, 80),
                    ManualPoint(10, 80),
                ],
                openings=[
                    ManualOpeningInput(width=0.9, height=2.0, dimension_unit="m"),
                ],
            )
        ]
    )

    opening = convert_manual_plan_to_aufmass(input_data).rooms[0].openings[0]

    assert opening.width == pytest.approx(0.9)
    assert opening.height == pytest.approx(2.0)


def test_invalid_calibration_identical_points_blocked() -> None:
    input_data = manual_input(
        calibration=ScaleCalibration(
            point_a=ManualPoint(1, 1),
            point_b=ManualPoint(1, 1),
            real_length_m=1.0,
        )
    )

    with pytest.raises(ValueError, match="calibration points must not be identical."):
        convert_manual_plan_to_aufmass(input_data)


def test_invalid_real_length_blocked() -> None:
    input_data = manual_input(
        calibration=ScaleCalibration(
            point_a=ManualPoint(0, 0),
            point_b=ManualPoint(1, 0),
            real_length_m=0,
        )
    )

    with pytest.raises(ValueError, match="calibration real_length_m must be > 0."):
        convert_manual_plan_to_aufmass(input_data)


def test_invalid_room_polygon_blocked() -> None:
    input_data = manual_input(
        rooms=[
            ManualRoomInput(
                room_id="room-1",
                height_m=2.5,
                polygon=[ManualPoint(0, 0), ManualPoint(1, 0)],
            )
        ]
    )

    with pytest.raises(ValueError, match="room room-1: polygon must have at least 3 points."):
        convert_manual_plan_to_aufmass(input_data)


def test_integration_with_calculate_aufmass_produces_expected_room_result() -> None:
    input_data = manual_input(
        rooms=[
            ManualRoomInput(
                room_id="room-1",
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
        ]
    )

    result = calculate_manual_plan_aufmass(input_data)

    assert result.project_id == "public-manual-test"
    assert result.unit == "m"
    assert result.rooms[0].gross_wall_area == pytest.approx(35.0)
    assert result.rooms[0].openings_area == pytest.approx(4.2)
    assert result.rooms[0].net_wall_area == pytest.approx(30.8)
    assert result.summary.total_floor_area == pytest.approx(12.0)


def test_schema_file_exists_and_contains_expected_top_level_fields() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.aufmass_manual_input.schema.json"
    assert schema["required"] == ["project_id", "calibration", "rooms"]
    assert set(schema["properties"]) >= {
        "project_id",
        "calibration",
        "rooms",
        "source_page",
        "source_ref",
        "confidence",
        "review_status",
    }
    assert set(schema["properties"]["rooms"]["items"]["properties"]) >= {
        "room_id",
        "height_m",
        "polygon",
        "openings",
        "source_page",
        "source_ref",
        "confidence",
        "review_status",
    }


def test_adapter_functions_have_no_file_network_subprocess_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    open_mock = Mock(side_effect=AssertionError("open must not be called"))
    urlopen_mock = Mock(side_effect=AssertionError("urlopen must not be called"))
    run_mock = Mock(side_effect=AssertionError("subprocess.run must not be called"))
    popen_mock = Mock(side_effect=AssertionError("subprocess.Popen must not be called"))
    monkeypatch.setattr(builtins, "open", open_mock)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen_mock)
    monkeypatch.setattr(subprocess, "run", run_mock)
    monkeypatch.setattr(subprocess, "Popen", popen_mock)

    input_data = manual_input()

    convert_manual_plan_to_aufmass(input_data)
    calculate_manual_plan_aufmass(input_data)

    open_mock.assert_not_called()
    urlopen_mock.assert_not_called()
    run_mock.assert_not_called()
    popen_mock.assert_not_called()
