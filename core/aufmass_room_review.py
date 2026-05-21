from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.aufmass_room_matcher import RoomMatchCandidate, RoomMatchResult


ROOM_REVIEW_COLUMNS = [
    "room_id",
    "room_name",
    "source_ref",
    "source_layer",
    "contour_id",
    "contour_status",
    "label_text",
    "label_status",
    "area_from_label_m2",
    "area_calculated_m2",
    "area_delta_m2",
    "area_delta_percent",
    "height_m",
    "height_source",
    "openings_status",
    "review_status",
    "operator_note",
    "export_ready",
]


@dataclass(frozen=True)
class AufmassRoomReviewRow:
    room_id: str
    room_name: str
    source_ref: str
    source_layer: str
    contour_id: str
    contour_status: str
    label_text: str
    label_status: str
    area_from_label_m2: Optional[float]
    area_calculated_m2: float
    area_delta_m2: Optional[float]
    area_delta_percent: Optional[float]
    height_m: Optional[float]
    height_source: str
    openings_status: str
    review_status: str
    operator_note: str
    export_ready: bool


@dataclass(frozen=True)
class AufmassRoomReviewTable:
    source_units: Optional[str]
    rows: list[AufmassRoomReviewRow]
    summary: dict[str, object]


def room_matches_to_review_table(room_matches: RoomMatchResult) -> AufmassRoomReviewTable:
    """Build deterministic public-safe review rows from DXF room match candidates."""
    _validate_room_matches(room_matches)
    contours_by_id = {contour.contour_id: contour for contour in room_matches.contours}
    rows = [
        _match_to_review_row(match, contours_by_id[match.contour_id].layer)
        for match in room_matches.matches
    ]
    return AufmassRoomReviewTable(
        source_units=room_matches.units,
        rows=rows,
        summary={
            "row_count": len(rows),
            "review_status_counts": _count_review_statuses(rows),
            "stage": "room_review_table",
            "official_quantities": False,
        },
    )


def room_review_table_to_rows(table: AufmassRoomReviewTable) -> list[dict[str, object]]:
    """Return spreadsheet-ready review rows in fixed column order."""
    _validate_table(table)
    return [_row_to_dict(row) for row in table.rows]


def room_review_table_to_dict(table: AufmassRoomReviewTable) -> dict[str, object]:
    """Return a JSON-compatible dictionary for the in-memory review table."""
    _validate_table(table)
    return {
        "source_units": table.source_units,
        "columns": list(ROOM_REVIEW_COLUMNS),
        "rows": room_review_table_to_rows(table),
        "summary": table.summary,
    }


def _validate_room_matches(room_matches: RoomMatchResult) -> None:
    if not isinstance(room_matches, RoomMatchResult):
        raise TypeError("room_matches must be a RoomMatchResult from core.aufmass_room_matcher.")
    contour_ids = {contour.contour_id for contour in room_matches.contours}
    missing_contours = [match.contour_id for match in room_matches.matches if match.contour_id not in contour_ids]
    if missing_contours:
        missing = ", ".join(missing_contours)
        raise ValueError(f"room match candidates reference missing contours: {missing}")


def _validate_table(table: AufmassRoomReviewTable) -> None:
    if not isinstance(table, AufmassRoomReviewTable):
        raise TypeError("table must be an AufmassRoomReviewTable.")


def _match_to_review_row(match: RoomMatchCandidate, source_layer: str) -> AufmassRoomReviewRow:
    return AufmassRoomReviewRow(
        room_id=match.match_id,
        room_name=match.room_label_text or "",
        source_ref=match.match_id,
        source_layer=source_layer,
        contour_id=match.contour_id,
        contour_status="candidate",
        label_text=match.room_label_text or "",
        label_status="linked" if match.room_label_id else "missing",
        area_from_label_m2=match.parsed_area,
        area_calculated_m2=match.calculated_area,
        area_delta_m2=match.area_delta,
        area_delta_percent=_area_delta_percent(match.area_delta, match.parsed_area),
        height_m=None,
        height_source="",
        openings_status="not_reviewed",
        review_status=match.status,
        operator_note=" ".join(match.review_notes),
        export_ready=False,
    )


def _area_delta_percent(area_delta: Optional[float], area_from_label: Optional[float]) -> Optional[float]:
    if area_delta is None or area_from_label in (None, 0):
        return None
    return area_delta / area_from_label * 100.0


def _count_review_statuses(rows: list[AufmassRoomReviewRow]) -> dict[str, int]:
    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row.review_status] = status_counts.get(row.review_status, 0) + 1
    return dict(sorted(status_counts.items()))


def _row_to_dict(row: AufmassRoomReviewRow) -> dict[str, object]:
    row_values: dict[str, Any] = {
        "room_id": row.room_id,
        "room_name": row.room_name,
        "source_ref": row.source_ref,
        "source_layer": row.source_layer,
        "contour_id": row.contour_id,
        "contour_status": row.contour_status,
        "label_text": row.label_text,
        "label_status": row.label_status,
        "area_from_label_m2": row.area_from_label_m2,
        "area_calculated_m2": row.area_calculated_m2,
        "area_delta_m2": row.area_delta_m2,
        "area_delta_percent": row.area_delta_percent,
        "height_m": row.height_m,
        "height_source": row.height_source,
        "openings_status": row.openings_status,
        "review_status": row.review_status,
        "operator_note": row.operator_note,
        "export_ready": row.export_ready,
    }
    return {column: row_values[column] for column in ROOM_REVIEW_COLUMNS}
