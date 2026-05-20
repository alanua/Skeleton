from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.aufmass_engine import (
    AufmassInput,
    Opening,
    Point,
    RoomInput,
    calculate_aufmass,
    polygon_area,
    polygon_perimeter,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "aufmass_input.schema.json"


def rectangle_room(**overrides: object) -> RoomInput:
    values = {
        "room_id": "room-1",
        "name": "Test Room",
        "height": 2.5,
        "polygon": [
            Point(0, 0),
            Point(4, 0),
            Point(4, 3),
            Point(0, 3),
        ],
        "openings": [],
    }
    values.update(overrides)
    return RoomInput(**values)


def aufmass_input(*rooms: RoomInput, unit: str = "m") -> AufmassInput:
    return AufmassInput(project_id="public-test", unit=unit, rooms=list(rooms))


def test_rectangle_room_area_and_perimeter() -> None:
    points = rectangle_room().polygon

    assert polygon_area(points) == pytest.approx(12.0)
    assert polygon_perimeter(points) == pytest.approx(14.0)


def test_l_shaped_polygon_area_and_perimeter() -> None:
    points = [
        Point(0, 0),
        Point(4, 0),
        Point(4, 1),
        Point(2, 1),
        Point(2, 3),
        Point(0, 3),
    ]

    assert polygon_area(points) == pytest.approx(8.0)
    assert polygon_perimeter(points) == pytest.approx(14.0)


def test_wall_gross_and_net_area_with_door_and_window_openings() -> None:
    room = rectangle_room(
        openings=[
            Opening(width=0.9, height=2.0, count=1, name="door"),
            Opening(width=1.2, height=1.0, count=2, name="window"),
        ]
    )

    result = calculate_aufmass(aufmass_input(room)).rooms[0]

    assert result.gross_wall_area == pytest.approx(35.0)
    assert result.openings_area == pytest.approx(4.2)
    assert result.net_wall_area == pytest.approx(30.8)


def test_volume_calculation() -> None:
    result = calculate_aufmass(aufmass_input(rectangle_room())).rooms[0]

    assert result.volume == pytest.approx(30.0)


def test_totals_across_multiple_rooms() -> None:
    second_room = rectangle_room(
        room_id="room-2",
        height=3.0,
        polygon=[
            Point(0, 0),
            Point(2, 0),
            Point(2, 2),
            Point(0, 2),
        ],
    )

    result = calculate_aufmass(aufmass_input(rectangle_room(), second_room))

    assert result.summary.room_count == 2
    assert result.summary.total_floor_area == pytest.approx(16.0)
    assert result.summary.total_ceiling_area == pytest.approx(16.0)
    assert result.summary.total_perimeter == pytest.approx(22.0)
    assert result.summary.total_gross_wall_area == pytest.approx(59.0)
    assert result.summary.total_openings_area == pytest.approx(0.0)
    assert result.summary.total_net_wall_area == pytest.approx(59.0)
    assert result.summary.total_volume == pytest.approx(42.0)


def test_invalid_polygon_blocked() -> None:
    room = rectangle_room(polygon=[Point(0, 0), Point(1, 0)])

    with pytest.raises(ValueError, match="room room-1: polygon must have at least 3 points."):
        calculate_aufmass(aufmass_input(room))


def test_invalid_height_blocked() -> None:
    room = rectangle_room(height=0)

    with pytest.raises(ValueError, match="room room-1: height must be > 0."):
        calculate_aufmass(aufmass_input(room))


def test_negative_opening_values_blocked() -> None:
    room = rectangle_room(openings=[Opening(width=-0.1, height=2.0, count=1)])

    with pytest.raises(ValueError, match="room room-1: opening width must be non-negative."):
        calculate_aufmass(aufmass_input(room))


def test_non_integer_opening_count_blocked() -> None:
    room = rectangle_room(openings=[Opening(width=0.9, height=2.0, count=1.5)])  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="room room-1: opening count must be an integer."):
        calculate_aufmass(aufmass_input(room))


def test_openings_larger_than_gross_wall_area_blocked() -> None:
    room = rectangle_room(openings=[Opening(width=100.0, height=100.0, count=1)])

    with pytest.raises(ValueError, match="room room-1: openings_area exceeds gross_wall_area."):
        calculate_aufmass(aufmass_input(room))


def test_explicit_unit_required() -> None:
    with pytest.raises(ValueError, match="unit is required."):
        calculate_aufmass(aufmass_input(rectangle_room(), unit=""))


def test_unsupported_unit_blocked() -> None:
    with pytest.raises(ValueError, match="unit must be one of: m."):
        calculate_aufmass(aufmass_input(rectangle_room(), unit="mm"))


def test_schema_file_exists_and_includes_expected_top_level_fields() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.aufmass_input.schema.json"
    assert schema["required"] == ["project_id", "unit", "rooms"]
    assert set(schema["properties"]) >= {"project_id", "unit", "rooms", "source", "confidence"}
    assert set(schema["properties"]["rooms"]["items"]["properties"]) >= {
        "room_id",
        "name",
        "height",
        "polygon",
        "openings",
        "source",
        "confidence",
    }
