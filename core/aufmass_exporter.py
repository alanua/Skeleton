from __future__ import annotations

import csv
from io import StringIO

from core.aufmass_engine import AufmassResult, RoomTakeoffResult


EXPORT_COLUMNS = [
    "row_type",
    "project_id",
    "unit",
    "room_id",
    "room_name",
    "floor_area",
    "ceiling_area",
    "perimeter",
    "gross_wall_area",
    "openings_area",
    "net_wall_area",
    "volume",
]

NUMERIC_COLUMNS = frozenset(
    {
        "floor_area",
        "ceiling_area",
        "perimeter",
        "gross_wall_area",
        "openings_area",
        "net_wall_area",
        "volume",
    }
)
CSV_DECIMAL_PLACES = 3


def aufmass_result_to_rows(result: AufmassResult) -> list[dict[str, object]]:
    """Return deterministic report rows for all rooms plus a summary row."""
    _validate_result(result)
    return [_room_to_report_row(result, room) for room in result.rooms] + [
        aufmass_result_to_summary_row(result)
    ]


def aufmass_result_to_summary_row(result: AufmassResult) -> dict[str, object]:
    """Return the deterministic summary report row."""
    _validate_result(result)
    summary = result.summary
    return {
        "row_type": "summary",
        "project_id": result.project_id,
        "unit": result.unit,
        "room_id": "",
        "room_name": "Summary",
        "floor_area": _round_report_number(summary.total_floor_area),
        "ceiling_area": _round_report_number(summary.total_ceiling_area),
        "perimeter": _round_report_number(summary.total_perimeter),
        "gross_wall_area": _round_report_number(summary.total_gross_wall_area),
        "openings_area": _round_report_number(summary.total_openings_area),
        "net_wall_area": _round_report_number(summary.total_net_wall_area),
        "volume": _round_report_number(summary.total_volume),
    }


def aufmass_result_to_csv(result: AufmassResult) -> str:
    """Return deterministic CSV text for all room rows plus one summary row."""
    _validate_result(result)
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in aufmass_result_to_rows(result):
        writer.writerow(_csv_row(row))
    return buffer.getvalue()


def aufmass_result_to_json_dict(result: AufmassResult) -> dict[str, object]:
    """Return a JSON-serializable dict preserving raw numeric result values."""
    _validate_result(result)
    return {
        "project_id": result.project_id,
        "unit": result.unit,
        "columns": list(EXPORT_COLUMNS),
        "rooms": [_room_to_json_dict(result, room) for room in result.rooms],
        "summary": _summary_to_json_dict(result),
    }


def _validate_result(result: AufmassResult) -> None:
    if not isinstance(result, AufmassResult):
        raise TypeError("result must be an AufmassResult from core.aufmass_engine.")


def _room_to_report_row(result: AufmassResult, room: RoomTakeoffResult) -> dict[str, object]:
    return {
        "row_type": "room",
        "project_id": result.project_id,
        "unit": result.unit,
        "room_id": room.room_id,
        "room_name": room.name or "",
        "floor_area": _round_report_number(room.floor_area),
        "ceiling_area": _round_report_number(room.ceiling_area),
        "perimeter": _round_report_number(room.perimeter),
        "gross_wall_area": _round_report_number(room.gross_wall_area),
        "openings_area": _round_report_number(room.openings_area),
        "net_wall_area": _round_report_number(room.net_wall_area),
        "volume": _round_report_number(room.volume),
    }


def _room_to_json_dict(result: AufmassResult, room: RoomTakeoffResult) -> dict[str, object]:
    return {
        "row_type": "room",
        "project_id": result.project_id,
        "unit": result.unit,
        "room_id": room.room_id,
        "room_name": room.name or "",
        "floor_area": room.floor_area,
        "ceiling_area": room.ceiling_area,
        "perimeter": room.perimeter,
        "gross_wall_area": room.gross_wall_area,
        "openings_area": room.openings_area,
        "net_wall_area": room.net_wall_area,
        "volume": room.volume,
    }


def _summary_to_json_dict(result: AufmassResult) -> dict[str, object]:
    summary = result.summary
    return {
        "row_type": "summary",
        "project_id": result.project_id,
        "unit": result.unit,
        "room_count": summary.room_count,
        "room_id": "",
        "room_name": "Summary",
        "floor_area": summary.total_floor_area,
        "ceiling_area": summary.total_ceiling_area,
        "perimeter": summary.total_perimeter,
        "gross_wall_area": summary.total_gross_wall_area,
        "openings_area": summary.total_openings_area,
        "net_wall_area": summary.total_net_wall_area,
        "volume": summary.total_volume,
    }


def _round_report_number(value: float) -> float:
    return round(value, CSV_DECIMAL_PLACES)


def _csv_row(row: dict[str, object]) -> dict[str, object]:
    csv_ready: dict[str, object] = {}
    for column in EXPORT_COLUMNS:
        value = row[column]
        if column in NUMERIC_COLUMNS:
            csv_ready[column] = f"{float(value):.{CSV_DECIMAL_PLACES}f}"
        else:
            csv_ready[column] = value
    return csv_ready
