from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import re
from typing import Any, Optional


AREA_MISMATCH_ABSOLUTE_TOLERANCE = 0.05
AREA_MISMATCH_RELATIVE_TOLERANCE = 0.02


@dataclass(frozen=True)
class RoomPoint:
    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True)
class RoomContourCandidate:
    contour_id: str
    layer: str
    entity_type: str
    source_index: int
    points: list[RoomPoint]
    centroid: RoomPoint
    bbox: dict[str, float]
    area: float


@dataclass(frozen=True)
class RoomLabelCandidate:
    label_id: str
    source_type: str
    source_index: int
    layer: str
    text: str
    insert: RoomPoint
    height: Optional[float] = None
    rotation: Optional[float] = None
    parsed_area: Optional[float] = None


@dataclass(frozen=True)
class RoomMatchCandidate:
    match_id: str
    contour_id: str
    room_label_id: Optional[str]
    area_label_id: Optional[str]
    room_label_text: Optional[str]
    area_label_text: Optional[str]
    calculated_area: float
    parsed_area: Optional[float]
    area_delta: Optional[float]
    status: str
    confidence: float
    review_notes: list[str] = field(default_factory=list)
    nearby_label_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RoomMatchResult:
    units: Optional[str]
    insunits: Optional[int]
    contours: list[RoomContourCandidate]
    labels: list[RoomLabelCandidate]
    matches: list[RoomMatchCandidate]
    summary: dict[str, object]


def match_dxf_rooms(dxf_result: Any) -> RoomMatchResult:
    """Match closed DXF polylines to nearby text labels from extracted data."""
    units = _optional_string(_get(dxf_result, "units"))
    insunits = _optional_int(_get(dxf_result, "insunits"))
    labels = _label_candidates(dxf_result)
    contours = _contour_candidates(dxf_result)
    labels_by_contour = _labels_by_contour(contours, labels)
    matches = _flag_duplicate_room_labels(
        [_match_contour(contour, labels_by_contour[contour.contour_id]) for contour in contours]
    )
    status_counts: dict[str, int] = {}
    for match in matches:
        status_counts[match.status] = status_counts.get(match.status, 0) + 1
    return RoomMatchResult(
        units=units,
        insunits=insunits,
        contours=contours,
        labels=labels,
        matches=matches,
        summary={
            "contour_count": len(contours),
            "label_count": len(labels),
            "match_count": len(matches),
            "status_counts": dict(sorted(status_counts.items())),
            "stage": "room_match_candidates",
        },
    )


def room_match_result_to_dict(result: RoomMatchResult) -> dict[str, object]:
    """Convert a room match result into a JSON-compatible dictionary."""
    return {
        "units": result.units,
        "insunits": result.insunits,
        "contours": [_contour_to_dict(contour) for contour in result.contours],
        "labels": [_label_to_dict(label) for label in result.labels],
        "matches": [_match_to_dict(match) for match in result.matches],
        "summary": result.summary,
    }


def parse_area_label(text: str) -> Optional[float]:
    normalized = text.replace("\u00b2", "2")
    patterns = [
        r"\bNGF\b\s*[:=]?\s*(?P<value>\d+(?:[.,]\d+)?)\s*(?:m\s*2|qm|m2)\b",
        r"\b(?P<value>\d+(?:[.,]\d+)?)\s*(?:m\s*2|qm|m2)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return float(match.group("value").replace(",", "."))
    return None


def _contour_candidates(dxf_result: Any) -> list[RoomContourCandidate]:
    contours: list[RoomContourCandidate] = []
    for source_index, polyline in enumerate(_get_sequence(dxf_result, "polylines")):
        if not bool(_get(polyline, "closed")):
            continue
        points = [_point(point) for point in _get_sequence(polyline, "points")]
        if len(points) < 3:
            continue
        area = _polygon_area(points)
        contours.append(
            RoomContourCandidate(
                contour_id=f"contour-{len(contours) + 1}",
                layer=_string_or_empty(_get(polyline, "layer")),
                entity_type=_string_or_empty(_get(polyline, "entity_type")),
                source_index=source_index,
                points=points,
                centroid=_polygon_centroid(points, area),
                bbox=_bbox(points),
                area=area,
            )
        )
    return contours


def _label_candidates(dxf_result: Any) -> list[RoomLabelCandidate]:
    labels: list[RoomLabelCandidate] = []
    for source_type, field_name in (("TEXT", "texts"), ("MTEXT", "mtexts")):
        for source_index, label in enumerate(_get_sequence(dxf_result, field_name)):
            text = _string_or_empty(_get(label, "text")).strip()
            if not text:
                continue
            labels.append(
                RoomLabelCandidate(
                    label_id=f"label-{len(labels) + 1}",
                    source_type=source_type,
                    source_index=source_index,
                    layer=_string_or_empty(_get(label, "layer")),
                    text=text,
                    insert=_point(_get(label, "insert")),
                    height=_optional_float(_get(label, "height")),
                    rotation=_optional_float(_get(label, "rotation")),
                    parsed_area=parse_area_label(text),
                )
            )
    return labels


def _match_contour(contour: RoomContourCandidate, labels: list[RoomLabelCandidate]) -> RoomMatchCandidate:
    ranked = sorted(
        ((label, _distance_to_polygon(label.insert, contour.points)) for label in labels),
        key=lambda item: (item[1], item[0].label_id),
    )
    area_ranked = [item for item in ranked if item[0].parsed_area is not None]
    room_ranked = [item for item in ranked if item[0].parsed_area is None]

    area_label = area_ranked[0][0] if area_ranked else None
    room_label = room_ranked[0][0] if room_ranked else None
    if room_label is None and area_label is not None and _has_non_area_text(area_label.text):
        room_label = area_label

    parsed_area = area_label.parsed_area if area_label else None
    area_delta = None if parsed_area is None else contour.area - parsed_area
    status = "candidate"
    confidence = 0.75
    review_notes: list[str] = []

    if room_label is None:
        status = "needs_review"
        confidence = 0.45
        review_notes.append("No non-area room label was linked to this closed polyline.")
    elif _has_distance_tie(room_ranked):
        status = _needs_review_status(status)
        confidence = min(confidence, 0.45)
        review_notes.append("Multiple room labels are equally close to this closed polyline.")
    if parsed_area is None:
        if status == "candidate":
            confidence = 0.65
        review_notes.append("No parseable area label was linked to this closed polyline.")
    elif _area_mismatch(contour.area, parsed_area):
        status = "area_mismatch"
        confidence = 0.5
        review_notes.append("Calculated polyline area differs from parsed label area.")

    return RoomMatchCandidate(
        match_id=f"match-{contour.contour_id.split('-')[-1]}",
        contour_id=contour.contour_id,
        room_label_id=room_label.label_id if room_label else None,
        area_label_id=area_label.label_id if area_label else None,
        room_label_text=room_label.text if room_label else None,
        area_label_text=area_label.text if area_label else None,
        calculated_area=contour.area,
        parsed_area=parsed_area,
        area_delta=area_delta,
        status=status,
        confidence=confidence,
        review_notes=review_notes,
        nearby_label_ids=[label.label_id for label, _distance in ranked[:5]],
    )


def _labels_by_contour(
    contours: list[RoomContourCandidate],
    labels: list[RoomLabelCandidate],
) -> dict[str, list[RoomLabelCandidate]]:
    labels_by_contour = {contour.contour_id: [] for contour in contours}
    for label in labels:
        distances = [
            (contour, _distance_to_polygon(label.insert, contour.points))
            for contour in contours
        ]
        if not distances:
            continue
        closest_distance = min(distance for _contour, distance in distances)
        for contour, distance in distances:
            if math.isclose(distance, closest_distance, abs_tol=1e-9):
                labels_by_contour[contour.contour_id].append(label)
    return labels_by_contour


def _flag_duplicate_room_labels(matches: list[RoomMatchCandidate]) -> list[RoomMatchCandidate]:
    matches_by_room_label: dict[str, list[RoomMatchCandidate]] = {}
    for match in matches:
        if match.room_label_text is None:
            continue
        normalized = match.room_label_text.strip().casefold()
        if normalized:
            matches_by_room_label.setdefault(normalized, []).append(match)

    duplicate_match_ids = {
        match.match_id
        for room_matches in matches_by_room_label.values()
        if len(room_matches) > 1
        for match in room_matches
    }
    return [
        _add_duplicate_room_label_note(match) if match.match_id in duplicate_match_ids else match
        for match in matches
    ]


def _add_duplicate_room_label_note(match: RoomMatchCandidate) -> RoomMatchCandidate:
    return replace(
        match,
        status=_needs_review_status(match.status),
        confidence=min(match.confidence, 0.45),
        review_notes=[*match.review_notes, "Room label text is duplicated across closed polylines."],
    )


def _has_distance_tie(ranked: list[tuple[RoomLabelCandidate, float]]) -> bool:
    if len(ranked) < 2:
        return False
    return math.isclose(ranked[0][1], ranked[1][1], abs_tol=1e-9)


def _needs_review_status(status: str) -> str:
    if status == "candidate":
        return "needs_review"
    return status


def _area_mismatch(calculated: float, parsed: float) -> bool:
    tolerance = max(AREA_MISMATCH_ABSOLUTE_TOLERANCE, abs(calculated) * AREA_MISMATCH_RELATIVE_TOLERANCE)
    return abs(calculated - parsed) > tolerance


def _has_non_area_text(text: str) -> bool:
    cleaned = re.sub(r"\bNGF\b\s*[:=]?", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\d+(?:[.,]\d+)?\s*(?:m\s*(?:2|\u00b2)|qm|m2)\b", "", cleaned, flags=re.IGNORECASE)
    return bool(cleaned.strip(" -_:;"))


def _polygon_area(points: list[RoomPoint]) -> float:
    doubled_area = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        doubled_area += point.x * next_point.y - next_point.x * point.y
    return abs(doubled_area) / 2.0


def _polygon_centroid(points: list[RoomPoint], area: float) -> RoomPoint:
    if area == 0:
        return RoomPoint(
            x=sum(point.x for point in points) / len(points),
            y=sum(point.y for point in points) / len(points),
            z=sum(point.z for point in points) / len(points),
        )
    cross_sum = 0.0
    centroid_x = 0.0
    centroid_y = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        cross = point.x * next_point.y - next_point.x * point.y
        cross_sum += cross
        centroid_x += (point.x + next_point.x) * cross
        centroid_y += (point.y + next_point.y) * cross
    if cross_sum == 0:
        return RoomPoint(x=points[0].x, y=points[0].y, z=points[0].z)
    return RoomPoint(x=centroid_x / (3 * cross_sum), y=centroid_y / (3 * cross_sum), z=0.0)


def _bbox(points: list[RoomPoint]) -> dict[str, float]:
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    return {"min_x": min(xs), "min_y": min(ys), "max_x": max(xs), "max_y": max(ys)}


def _distance_to_polygon(point: RoomPoint, polygon: list[RoomPoint]) -> float:
    if _point_in_polygon(point, polygon):
        return 0.0
    return min(_distance_to_segment(point, start, polygon[(index + 1) % len(polygon)]) for index, start in enumerate(polygon))


def _point_in_polygon(point: RoomPoint, polygon: list[RoomPoint]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _distance_to_segment(point, previous, current) <= 1e-9:
            return True
        intersects = (current.y > point.y) != (previous.y > point.y)
        if intersects:
            x_intersection = (previous.x - current.x) * (point.y - current.y) / (previous.y - current.y) + current.x
            if point.x < x_intersection:
                inside = not inside
        previous = current
    return inside


def _distance_to_segment(point: RoomPoint, start: RoomPoint, end: RoomPoint) -> float:
    dx = end.x - start.x
    dy = end.y - start.y
    if dx == 0 and dy == 0:
        return math.hypot(point.x - start.x, point.y - start.y)
    t = ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    projection_x = start.x + t * dx
    projection_y = start.y + t * dy
    return math.hypot(point.x - projection_x, point.y - projection_y)


def _point(value: Any) -> RoomPoint:
    return RoomPoint(
        x=float(_get(value, "x")),
        y=float(_get(value, "y")),
        z=float(_get(value, "z", 0.0)),
    )


def _get_sequence(value: Any, key: str) -> list[Any]:
    sequence = _get(value, key, [])
    if sequence is None:
        return []
    return list(sequence)


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    return int(value)


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _point_to_dict(point: RoomPoint) -> dict[str, float]:
    return {"x": point.x, "y": point.y, "z": point.z}


def _contour_to_dict(contour: RoomContourCandidate) -> dict[str, object]:
    return {
        "contour_id": contour.contour_id,
        "layer": contour.layer,
        "entity_type": contour.entity_type,
        "source_index": contour.source_index,
        "points": [_point_to_dict(point) for point in contour.points],
        "centroid": _point_to_dict(contour.centroid),
        "bbox": contour.bbox,
        "area": contour.area,
    }


def _label_to_dict(label: RoomLabelCandidate) -> dict[str, object]:
    return {
        "label_id": label.label_id,
        "source_type": label.source_type,
        "source_index": label.source_index,
        "layer": label.layer,
        "text": label.text,
        "insert": _point_to_dict(label.insert),
        "height": label.height,
        "rotation": label.rotation,
        "parsed_area": label.parsed_area,
    }


def _match_to_dict(match: RoomMatchCandidate) -> dict[str, object]:
    return {
        "match_id": match.match_id,
        "contour_id": match.contour_id,
        "room_label_id": match.room_label_id,
        "area_label_id": match.area_label_id,
        "room_label_text": match.room_label_text,
        "area_label_text": match.area_label_text,
        "calculated_area": match.calculated_area,
        "parsed_area": match.parsed_area,
        "area_delta": match.area_delta,
        "status": match.status,
        "confidence": match.confidence,
        "review_notes": match.review_notes,
        "nearby_label_ids": match.nearby_label_ids,
    }
