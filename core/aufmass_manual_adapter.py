from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Optional

from core.aufmass_engine import (
    AufmassInput,
    AufmassResult,
    Opening,
    Point,
    RoomInput,
    calculate_aufmass,
)


SUPPORTED_OPENING_DIMENSION_UNITS = frozenset({"drawing", "m"})


@dataclass(frozen=True)
class ManualPoint:
    x: float
    y: float


@dataclass(frozen=True)
class ScaleCalibration:
    point_a: ManualPoint
    point_b: ManualPoint
    real_length_m: float
    source_page: Optional[str] = None
    source_ref: Optional[str] = None
    confidence: Optional[float] = None
    review_status: Optional[str] = None


@dataclass(frozen=True)
class ManualOpeningInput:
    width: float
    height: float
    count: int = 1
    opening_id: Optional[str] = None
    name: Optional[str] = None
    dimension_unit: str = "drawing"
    source_page: Optional[str] = None
    source_ref: Optional[str] = None
    confidence: Optional[float] = None
    review_status: Optional[str] = None


@dataclass(frozen=True)
class ManualRoomInput:
    room_id: str
    height_m: float
    polygon: list[ManualPoint]
    openings: list[ManualOpeningInput] = field(default_factory=list)
    name: Optional[str] = None
    source_page: Optional[str] = None
    source_ref: Optional[str] = None
    confidence: Optional[float] = None
    review_status: Optional[str] = None


@dataclass(frozen=True)
class ManualAufmassInput:
    project_id: str
    calibration: ScaleCalibration
    rooms: list[ManualRoomInput]
    source_page: Optional[str] = None
    source_ref: Optional[str] = None
    confidence: Optional[float] = None
    review_status: Optional[str] = None


@dataclass(frozen=True)
class ManualAdapterAudit:
    calibration_distance_units: float
    scale_m_per_unit: float
    origin: Point
    unit: str = "m"


@dataclass(frozen=True)
class ManualAdapterResult:
    aufmass_input: AufmassInput
    audit: ManualAdapterAudit


def convert_manual_plan_to_aufmass(input_data: ManualAufmassInput) -> AufmassInput:
    """Convert manually marked drawing coordinates into metric Aufmass input."""
    return convert_manual_plan(input_data).aufmass_input


def calculate_manual_plan_aufmass(input_data: ManualAufmassInput) -> AufmassResult:
    """Convert manual drawing input and calculate deterministic Aufmass quantities."""
    return calculate_aufmass(convert_manual_plan_to_aufmass(input_data))


def convert_manual_plan(input_data: ManualAufmassInput) -> ManualAdapterResult:
    _validate_manual_input(input_data)
    calibration_distance_units = _distance(
        input_data.calibration.point_a,
        input_data.calibration.point_b,
    )
    scale_m_per_unit = input_data.calibration.real_length_m / calibration_distance_units
    origin = input_data.calibration.point_a

    rooms = [
        RoomInput(
            room_id=room.room_id,
            height=room.height_m,
            polygon=[_convert_point(point, origin, scale_m_per_unit) for point in room.polygon],
            openings=[_convert_opening(room, opening, scale_m_per_unit) for opening in room.openings],
            name=room.name,
            source=_source_label(room.source_page, room.source_ref, room.review_status),
            confidence=room.confidence,
        )
        for room in input_data.rooms
    ]

    aufmass_input = AufmassInput(
        project_id=input_data.project_id,
        unit="m",
        rooms=rooms,
        source=_source_label(input_data.source_page, input_data.source_ref, input_data.review_status),
        confidence=input_data.confidence,
    )
    audit = ManualAdapterAudit(
        calibration_distance_units=calibration_distance_units,
        scale_m_per_unit=scale_m_per_unit,
        origin=Point(origin.x, origin.y),
    )
    return ManualAdapterResult(aufmass_input=aufmass_input, audit=audit)


def _validate_manual_input(input_data: ManualAufmassInput) -> None:
    _validate_calibration(input_data.calibration)
    if not input_data.rooms:
        raise ValueError("rooms must contain at least one room.")
    for room in input_data.rooms:
        _validate_room(room)


def _validate_calibration(calibration: ScaleCalibration) -> None:
    _validate_point(calibration.point_a, "calibration point_a")
    _validate_point(calibration.point_b, "calibration point_b")
    _validate_finite_number(calibration.real_length_m, "calibration real_length_m")
    if calibration.real_length_m <= 0:
        raise ValueError("calibration real_length_m must be > 0.")
    if _distance(calibration.point_a, calibration.point_b) == 0:
        raise ValueError("calibration points must not be identical.")


def _validate_room(room: ManualRoomInput) -> None:
    _validate_finite_number(room.height_m, f"room {room.room_id}: height_m")
    if room.height_m <= 0:
        raise ValueError(f"room {room.room_id}: height_m must be > 0.")
    if len(room.polygon) < 3:
        raise ValueError(f"room {room.room_id}: polygon must have at least 3 points.")
    for index, point in enumerate(room.polygon):
        _validate_point(point, f"room {room.room_id}: polygon[{index}]")
    for opening in room.openings:
        _validate_opening(room.room_id, opening)


def _validate_opening(room_id: str, opening: ManualOpeningInput) -> None:
    _validate_finite_number(opening.width, f"room {room_id}: opening width")
    _validate_finite_number(opening.height, f"room {room_id}: opening height")
    if opening.width < 0:
        raise ValueError(f"room {room_id}: opening width must be non-negative.")
    if opening.height < 0:
        raise ValueError(f"room {room_id}: opening height must be non-negative.")
    if opening.dimension_unit not in SUPPORTED_OPENING_DIMENSION_UNITS:
        raise ValueError(f"room {room_id}: opening dimension_unit must be one of: drawing, m.")


def _validate_point(point: ManualPoint, label: str) -> None:
    _validate_finite_number(point.x, f"{label}.x")
    _validate_finite_number(point.y, f"{label}.y")


def _validate_finite_number(value: float, label: str) -> None:
    if not isfinite(value):
        raise ValueError(f"{label} must be a finite number.")


def _convert_point(point: ManualPoint, origin: ManualPoint, scale_m_per_unit: float) -> Point:
    return Point(
        x=(point.x - origin.x) * scale_m_per_unit,
        y=(point.y - origin.y) * scale_m_per_unit,
    )


def _convert_opening(
    room: ManualRoomInput,
    opening: ManualOpeningInput,
    scale_m_per_unit: float,
) -> Opening:
    if opening.dimension_unit == "m":
        width = opening.width
        height = opening.height
    else:
        width = opening.width * scale_m_per_unit
        height = opening.height * scale_m_per_unit

    return Opening(
        width=width,
        height=height,
        count=opening.count,
        opening_id=opening.opening_id,
        name=opening.name,
        source=_source_label(opening.source_page, opening.source_ref, opening.review_status),
        confidence=opening.confidence if opening.confidence is not None else room.confidence,
    )


def _distance(point_a: ManualPoint, point_b: ManualPoint) -> float:
    return ((point_b.x - point_a.x) ** 2 + (point_b.y - point_a.y) ** 2) ** 0.5


def _source_label(
    source_page: Optional[str],
    source_ref: Optional[str],
    review_status: Optional[str],
) -> Optional[str]:
    parts = []
    if source_page:
        parts.append(f"page={source_page}")
    if source_ref:
        parts.append(f"ref={source_ref}")
    if review_status:
        parts.append(f"review={review_status}")
    if not parts:
        return None
    return "; ".join(parts)
