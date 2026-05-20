from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


SUPPORTED_UNITS = frozenset({"m"})
FLOAT_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Opening:
    width: float
    height: float
    count: int = 1
    opening_id: Optional[str] = None
    name: Optional[str] = None
    source: Optional[str] = None
    confidence: Optional[float] = None


@dataclass(frozen=True)
class RoomInput:
    room_id: str
    height: float
    polygon: list[Point]
    openings: list[Opening] = field(default_factory=list)
    name: Optional[str] = None
    source: Optional[str] = None
    confidence: Optional[float] = None


@dataclass(frozen=True)
class AufmassInput:
    project_id: str
    unit: str
    rooms: list[RoomInput]
    source: Optional[str] = None
    confidence: Optional[float] = None


@dataclass(frozen=True)
class RoomTakeoffResult:
    room_id: str
    name: Optional[str]
    floor_area: float
    ceiling_area: float
    perimeter: float
    gross_wall_area: float
    openings_area: float
    net_wall_area: float
    volume: float


@dataclass(frozen=True)
class AufmassSummary:
    room_count: int
    total_floor_area: float
    total_ceiling_area: float
    total_perimeter: float
    total_gross_wall_area: float
    total_openings_area: float
    total_net_wall_area: float
    total_volume: float


@dataclass(frozen=True)
class AufmassResult:
    project_id: str
    unit: str
    rooms: list[RoomTakeoffResult]
    summary: AufmassSummary


def polygon_area(points: list[Point]) -> float:
    _validate_polygon(points, "polygon")
    doubled_area = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        doubled_area += point.x * next_point.y - next_point.x * point.y
    return abs(doubled_area) / 2.0


def polygon_perimeter(points: list[Point]) -> float:
    _validate_polygon(points, "polygon")
    perimeter = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        perimeter += ((next_point.x - point.x) ** 2 + (next_point.y - point.y) ** 2) ** 0.5
    return perimeter


def calculate_aufmass(input_data: AufmassInput) -> AufmassResult:
    """Calculate deterministic room takeoff quantities from explicit geometry."""
    _validate_input(input_data)

    room_results = [_calculate_room(room) for room in input_data.rooms]
    summary = AufmassSummary(
        room_count=len(room_results),
        total_floor_area=sum(room.floor_area for room in room_results),
        total_ceiling_area=sum(room.ceiling_area for room in room_results),
        total_perimeter=sum(room.perimeter for room in room_results),
        total_gross_wall_area=sum(room.gross_wall_area for room in room_results),
        total_openings_area=sum(room.openings_area for room in room_results),
        total_net_wall_area=sum(room.net_wall_area for room in room_results),
        total_volume=sum(room.volume for room in room_results),
    )
    return AufmassResult(
        project_id=input_data.project_id,
        unit=input_data.unit,
        rooms=room_results,
        summary=summary,
    )


def _calculate_room(room: RoomInput) -> RoomTakeoffResult:
    floor_area = polygon_area(room.polygon)
    ceiling_area = floor_area
    perimeter = polygon_perimeter(room.polygon)
    gross_wall_area = perimeter * room.height
    openings_area = sum(opening.width * opening.height * opening.count for opening in room.openings)

    if openings_area - gross_wall_area > FLOAT_TOLERANCE:
        raise ValueError(f"room {room.room_id}: openings_area exceeds gross_wall_area.")

    net_wall_area = gross_wall_area - openings_area
    if net_wall_area < 0 and abs(net_wall_area) <= FLOAT_TOLERANCE:
        net_wall_area = 0.0
    if net_wall_area < 0:
        raise ValueError(f"room {room.room_id}: net_wall_area below 0.")

    return RoomTakeoffResult(
        room_id=room.room_id,
        name=room.name,
        floor_area=floor_area,
        ceiling_area=ceiling_area,
        perimeter=perimeter,
        gross_wall_area=gross_wall_area,
        openings_area=openings_area,
        net_wall_area=net_wall_area,
        volume=floor_area * room.height,
    )


def _validate_input(input_data: AufmassInput) -> None:
    if input_data.unit is None or input_data.unit == "":
        raise ValueError("unit is required.")
    if input_data.unit not in SUPPORTED_UNITS:
        raise ValueError(f"unit must be one of: {', '.join(sorted(SUPPORTED_UNITS))}.")
    if not input_data.rooms:
        raise ValueError("rooms must contain at least one room.")

    for room in input_data.rooms:
        _validate_room(room)


def _validate_room(room: RoomInput) -> None:
    if room.height <= 0:
        raise ValueError(f"room {room.room_id}: height must be > 0.")
    _validate_polygon(room.polygon, f"room {room.room_id}: polygon")

    for opening in room.openings:
        _validate_opening(room.room_id, opening)


def _validate_polygon(points: list[Point], label: str) -> None:
    if len(points) < 3:
        raise ValueError(f"{label} must have at least 3 points.")


def _validate_opening(room_id: str, opening: Opening) -> None:
    if opening.width < 0:
        raise ValueError(f"room {room_id}: opening width must be non-negative.")
    if opening.height < 0:
        raise ValueError(f"room {room_id}: opening height must be non-negative.")
    if isinstance(opening.count, bool) or not isinstance(opening.count, int):
        raise ValueError(f"room {room_id}: opening count must be an integer.")
    if opening.count < 0:
        raise ValueError(f"room {room_id}: opening count must be non-negative.")
