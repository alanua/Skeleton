from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
from typing import Optional


SUPPORTED_UNITS = frozenset({"m"})


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Opening:
    width: float
    height: float
    count: int = 1
    source: Optional[str] = None
    confidence: Optional[float] = None


@dataclass(frozen=True)
class RoomInput:
    room_id: str
    height: float
    polygon: tuple[Point, ...]
    name: Optional[str] = None
    openings: tuple[Opening, ...] = field(default_factory=tuple)
    source: Optional[str] = None
    confidence: Optional[float] = None


@dataclass(frozen=True)
class AufmassInput:
    project_id: str
    unit: str
    rooms: tuple[RoomInput, ...]
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
    floor_area: float
    ceiling_area: float
    perimeter: float
    gross_wall_area: float
    openings_area: float
    net_wall_area: float
    volume: float


@dataclass(frozen=True)
class AufmassResult:
    project_id: str
    unit: str
    rooms: tuple[RoomTakeoffResult, ...]
    summary: AufmassSummary


def polygon_area(points: tuple[Point, ...]) -> float:
    """Return polygon area using the shoelace formula."""
    _validate_polygon(points, "polygon")
    double_area = 0.0
    for current, next_point in _closed_edges(points):
        double_area += current.x * next_point.y
        double_area -= next_point.x * current.y
    return abs(double_area) / 2.0


def polygon_perimeter(points: tuple[Point, ...]) -> float:
    _validate_polygon(points, "polygon")
    return sum(
        hypot(next_point.x - current.x, next_point.y - current.y)
        for current, next_point in _closed_edges(points)
    )


def calculate_aufmass(input_data: AufmassInput) -> AufmassResult:
    """Calculate deterministic Aufmass quantities from explicit room geometry."""
    _validate_input(input_data)

    room_results = tuple(_calculate_room(room) for room in input_data.rooms)
    summary = AufmassSummary(
        room_count=len(room_results),
        floor_area=sum(room.floor_area for room in room_results),
        ceiling_area=sum(room.ceiling_area for room in room_results),
        perimeter=sum(room.perimeter for room in room_results),
        gross_wall_area=sum(room.gross_wall_area for room in room_results),
        openings_area=sum(room.openings_area for room in room_results),
        net_wall_area=sum(room.net_wall_area for room in room_results),
        volume=sum(room.volume for room in room_results),
    )
    return AufmassResult(
        project_id=input_data.project_id,
        unit=input_data.unit,
        rooms=room_results,
        summary=summary,
    )


def _calculate_room(room: RoomInput) -> RoomTakeoffResult:
    floor_area = polygon_area(room.polygon)
    perimeter = polygon_perimeter(room.polygon)
    gross_wall_area = perimeter * room.height
    openings_area = sum(opening.width * opening.height * opening.count for opening in room.openings)
    if openings_area > gross_wall_area:
        raise ValueError(f"room {room.room_id!r} openings_area must not exceed gross_wall_area.")

    return RoomTakeoffResult(
        room_id=room.room_id,
        name=room.name,
        floor_area=floor_area,
        ceiling_area=floor_area,
        perimeter=perimeter,
        gross_wall_area=gross_wall_area,
        openings_area=openings_area,
        net_wall_area=max(gross_wall_area - openings_area, 0.0),
        volume=floor_area * room.height,
    )


def _validate_input(input_data: AufmassInput) -> None:
    if input_data.unit not in SUPPORTED_UNITS:
        raise ValueError("unit must be explicit and supported: m.")

    for room in input_data.rooms:
        _validate_room(room)


def _validate_room(room: RoomInput) -> None:
    _validate_polygon(room.polygon, f"room {room.room_id!r} polygon")
    if room.height <= 0:
        raise ValueError(f"room {room.room_id!r} height must be > 0.")

    for index, opening in enumerate(room.openings):
        _validate_opening(opening, room.room_id, index)


def _validate_polygon(points: tuple[Point, ...], label: str) -> None:
    if len(points) < 3:
        raise ValueError(f"{label} must have at least 3 points.")


def _validate_opening(opening: Opening, room_id: str, index: int) -> None:
    prefix = f"room {room_id!r} opening {index}"
    if opening.width < 0:
        raise ValueError(f"{prefix} width must be non-negative.")
    if opening.height < 0:
        raise ValueError(f"{prefix} height must be non-negative.")
    if not isinstance(opening.count, int) or isinstance(opening.count, bool):
        raise ValueError(f"{prefix} count must be an integer.")
    if opening.count < 0:
        raise ValueError(f"{prefix} count must be non-negative.")


def _closed_edges(points: tuple[Point, ...]) -> tuple[tuple[Point, Point], ...]:
    return tuple((points[index], points[(index + 1) % len(points)]) for index in range(len(points)))
