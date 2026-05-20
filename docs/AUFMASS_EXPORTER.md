# Aufmass Exporter

## Purpose

`core/aufmass_exporter.py` converts an `AufmassResult` from `core/aufmass_engine.py` into deterministic review output.

Stage 1 returns only in-memory table rows, CSV text, and JSON-compatible dictionaries. It does not write files, read files, upload files, call subprocesses, call the network, parse drawings, or touch private source material.

## Stable Columns

Report rows and CSV output use this fixed column order:

1. `row_type`
2. `project_id`
3. `unit`
4. `room_id`
5. `room_name`
6. `floor_area`
7. `ceiling_area`
8. `perimeter`
9. `gross_wall_area`
10. `openings_area`
11. `net_wall_area`
12. `volume`

`row_type` is `room` for room rows and `summary` for the project total row. Room rows are emitted in the same order as `AufmassResult.rooms`. The summary row is emitted last.

Report rows round numeric quantity values to 3 decimal places. CSV text formats numeric quantity values with exactly 3 decimal places. JSON output preserves the raw numeric float values from the engine.

## CSV Output

The stable CSV entry point is:

```python
aufmass_result_to_csv(result: AufmassResult) -> str
```

CSV output uses the Python standard library `csv` module, includes a header row, includes every room row, and appends one summary row. The function returns CSV text as a string and never writes a file.

## JSON Output

The stable JSON-compatible entry point is:

```python
aufmass_result_to_json_dict(result: AufmassResult) -> dict[str, object]
```

The returned dictionary contains:

- `project_id`
- `unit`
- `columns`
- `rooms`
- `summary`

The JSON export is serializable with `json.dumps`. It does not call `json.dump` and does not write a file.

## Synthetic Example

Input is assumed to come from the engine:

```python
from core.aufmass_engine import AufmassInput, Point, RoomInput, calculate_aufmass
from core.aufmass_exporter import aufmass_result_to_csv, aufmass_result_to_json_dict

result = calculate_aufmass(
    AufmassInput(
        project_id="public-demo",
        unit="m",
        rooms=[
            RoomInput(
                room_id="room-001",
                name="Example Room",
                height=2.5,
                polygon=[
                    Point(0, 0),
                    Point(4, 0),
                    Point(4, 3),
                    Point(0, 3),
                ],
            )
        ],
    )
)

csv_text = aufmass_result_to_csv(result)
json_dict = aufmass_result_to_json_dict(result)
```

CSV:

```csv
row_type,project_id,unit,room_id,room_name,floor_area,ceiling_area,perimeter,gross_wall_area,openings_area,net_wall_area,volume
room,public-demo,m,room-001,Example Room,12.000,12.000,14.000,35.000,0.000,35.000,30.000
summary,public-demo,m,,Summary,12.000,12.000,14.000,35.000,0.000,35.000,30.000
```

JSON shape:

```json
{
  "project_id": "public-demo",
  "unit": "m",
  "columns": ["row_type", "project_id", "unit", "room_id", "room_name", "floor_area", "ceiling_area", "perimeter", "gross_wall_area", "openings_area", "net_wall_area", "volume"],
  "rooms": [
    {
      "row_type": "room",
      "project_id": "public-demo",
      "unit": "m",
      "room_id": "room-001",
      "room_name": "Example Room",
      "floor_area": 12.0,
      "ceiling_area": 12.0,
      "perimeter": 14.0,
      "gross_wall_area": 35.0,
      "openings_area": 0.0,
      "net_wall_area": 35.0,
      "volume": 30.0
    }
  ],
  "summary": {
    "row_type": "summary",
    "project_id": "public-demo",
    "unit": "m",
    "room_count": 1,
    "room_id": "",
    "room_name": "Summary",
    "floor_area": 12.0,
    "ceiling_area": 12.0,
    "perimeter": 14.0,
    "gross_wall_area": 35.0,
    "openings_area": 0.0,
    "net_wall_area": 35.0,
    "volume": 30.0
  }
}
```

## Limitations

This exporter does not calculate geometry. It only formats `AufmassResult` values already produced by the engine.

It does not parse PDF, scans, images, DWG, DXF, IFC, or spreadsheets. It adds no OCR, CAD extraction, image processing, file upload, runtime automation, network access, subprocess calls, or persistent storage.

## Privacy Boundary

The exporter accepts only an in-memory `AufmassResult`. It does not accept private source files or paths. All examples are synthetic public-safe data.

Do not commit real drawings, customer identifiers, addresses, private project quantities, contracts, invoices, photos, or source files. Private pilots with real PDFs or scans must run outside the public repo unless the operator explicitly approves a sanitized public-safe sample.
