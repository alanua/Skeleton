# Aufmass Engine

## Purpose

`core/aufmass_engine.py` is the stage 1 calculation core for public-safe Aufmass quantities. It accepts explicit room geometry, room heights, and openings, then returns deterministic room and project totals.

Drawing import is outside this stage. Later adapters may parse private source material and feed sanitized geometry into this engine.

## Input JSON Example

```json
{
  "project_id": "public-demo",
  "unit": "m",
  "source": "manual_public_example",
  "confidence": 1.0,
  "rooms": [
    {
      "room_id": "room-001",
      "name": "Example Room",
      "height": 2.5,
      "polygon": [
        {"x": 0, "y": 0},
        {"x": 4, "y": 0},
        {"x": 4, "y": 3},
        {"x": 0, "y": 3}
      ],
      "openings": [
        {"opening_id": "door-001", "name": "Door", "width": 0.9, "height": 2.0, "count": 1},
        {"opening_id": "window-001", "name": "Window", "width": 1.2, "height": 1.0, "count": 2}
      ]
    }
  ]
}
```

The stable entry point is:

```python
calculate_aufmass(input_data: AufmassInput) -> AufmassResult
```

## Output Fields

Each `RoomTakeoffResult` contains:

- `floor_area`
- `ceiling_area`
- `perimeter`
- `gross_wall_area`
- `openings_area`
- `net_wall_area`
- `volume`

`AufmassSummary` contains room count and totals for the same quantity fields across all rooms.

## Formulas

- `polygon_area(points)`: shoelace formula, returned as an absolute area.
- `polygon_perimeter(points)`: sum of edge lengths, including the closing edge.
- `room_floor_area = polygon_area(room.polygon)`
- `room_ceiling_area = room_floor_area`
- `gross_wall_area = perimeter * height`
- `openings_area = sum(width * height * count)`
- `net_wall_area = gross_wall_area - openings_area`
- `volume = floor_area * height`

## Validation Rules

- `unit` is required and currently supports `m` only.
- Each polygon must contain at least 3 points.
- Room height must be greater than 0.
- Opening width, height, and count must be non-negative.
- Opening count must be an integer.
- Opening area must not exceed gross wall area.
- Net wall area must not go below 0.
- Invalid input raises `ValueError` with deterministic messages.

## Limitations

This stage does not infer geometry, detect walls, calibrate scale, classify openings, or resolve conflicting source drawings. It assumes the caller already has explicit room coordinates and dimensions in meters.

Stage 1 does not parse DWG, DXF, PDF, IFC, scans, images, or uploads. It adds no OCR, CAD extraction, image processing, runtime automation, or live data access.

## Privacy Boundary

This module contains only public-safe calculation logic and synthetic examples. Do not commit real drawings, customer identifiers, addresses, private project quantities, contracts, invoices, photos, or source files.

Future private adapters must keep source files and private quantities outside the public repo unless the operator explicitly approves a sanitized public-safe sample.
