# Aufmass Stage 1 Calculation Engine

## Purpose

`core/aufmass_engine.py` is the public-safe calculation core for Aufmass room takeoff quantities. It accepts explicit room geometry, room heights, and openings, then returns deterministic floor, ceiling, perimeter, wall, opening, net wall, volume, and total quantities.

This stage is deliberately limited to calculation. Future private adapters may feed geometry into this engine after extracting or calibrating it elsewhere.

## Input JSON Example

```json
{
  "project_id": "public-safe-example",
  "unit": "m",
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
        {"width": 0.9, "height": 2.0, "count": 1},
        {"width": 1.2, "height": 1.0, "count": 2}
      ],
      "source": "manual_public_example",
      "confidence": 1.0
    }
  ]
}
```

## Output Fields

`calculate_aufmass(input_data: AufmassInput) -> AufmassResult` returns:

- `project_id`
- `unit`
- `rooms`: one `RoomTakeoffResult` per room.
- `summary`: totals across all rooms.

Each room result includes:

- `floor_area`
- `ceiling_area`
- `perimeter`
- `gross_wall_area`
- `openings_area`
- `net_wall_area`
- `volume`

## Formulas

- `polygon_area(points)`: shoelace formula using the closed room polygon.
- `polygon_perimeter(points)`: sum of all polygon edge lengths, including the closing edge.
- `floor_area = polygon_area`
- `ceiling_area = floor_area`
- `gross_wall_area = perimeter * height`
- `openings_area = sum(width * height * count)`
- `net_wall_area = gross_wall_area - openings_area`
- `volume = floor_area * height`
- Summary totals are the sum of each room result field.

## Validation Rules

- `unit` must be explicit and supported. Stage 1 supports `m` only.
- Each polygon must have at least 3 points.
- Room `height` must be greater than 0.
- Opening `width`, `height`, and `count` must be non-negative.
- Opening `count` must be an integer.
- `openings_area` must not exceed `gross_wall_area`.
- `net_wall_area` must not go below 0.
- Invalid input raises `ValueError` with deterministic messages.

## Limitations

Stage 1 does not parse DWG, DXF, PDF, IFC, scans, images, or OCR output. It does not calibrate scale, inspect drawings, upload files, call networks, run live automation, or store private project data.

The engine expects geometry that has already been reviewed and expressed as room polygons in meters.

## Privacy Boundary

This module and its tests use synthetic public-safe geometry only. Do not add real customer drawings, addresses, project names, private measured quantities, contracts, invoices, photos, or files from real construction projects.

Future adapters must keep private source material outside the public repo unless the operator explicitly approves a sanitized public-safe sample.
