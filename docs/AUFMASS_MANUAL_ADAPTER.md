# Aufmass Manual Adapter

## Purpose

`core/aufmass_manual_adapter.py` is the stage 1 bridge from manually marked drawing coordinates to the deterministic Aufmass calculation engine.

It accepts operator-marked scale points, room contour points, room heights, and openings. It converts drawing or pixel units into meters, builds `AufmassInput`, and hands that input to `core/aufmass_engine.py`.

## Expected Manual Input Flow

1. The operator marks two points on a known scale segment in the drawing.
2. The operator enters the real length of that segment in meters.
3. The operator marks each room polygon as drawing or pixel coordinates.
4. The operator enters each room height in meters.
5. The operator enters openings either in drawing units or explicitly in meters.
6. The adapter converts the marked values and calls `calculate_aufmass`.

## Calibration Formula

The adapter calculates:

```text
calibration_distance_units = sqrt((point_b.x - point_a.x)^2 + (point_b.y - point_a.y)^2)
scale_m_per_unit = real_length_m / calibration_distance_units
```

Each room polygon point is transformed relative to calibration `point_a`:

```text
x_m = (x_units - point_a.x) * scale_m_per_unit
y_m = (y_units - point_a.y) * scale_m_per_unit
```

Opening width and height are multiplied by `scale_m_per_unit` unless `dimension_unit` is explicitly `m`.

## Input JSON Example

```json
{
  "project_id": "public-manual-demo",
  "source_page": "1",
  "source_ref": "manual-public-example",
  "confidence": 0.8,
  "review_status": "needs_review",
  "calibration": {
    "point_a": {"x": 10, "y": 20},
    "point_b": {"x": 110, "y": 20},
    "real_length_m": 5.0,
    "source_page": "1",
    "source_ref": "scale-line",
    "confidence": 0.9,
    "review_status": "reviewed"
  },
  "rooms": [
    {
      "room_id": "room-001",
      "name": "Example Room",
      "height_m": 2.5,
      "source_page": "1",
      "source_ref": "room-outline",
      "confidence": 0.8,
      "review_status": "needs_review",
      "polygon": [
        {"x": 10, "y": 20},
        {"x": 90, "y": 20},
        {"x": 90, "y": 80},
        {"x": 10, "y": 80}
      ],
      "openings": [
        {
          "opening_id": "door-001",
          "name": "Door",
          "width": 18,
          "height": 40,
          "count": 1,
          "dimension_unit": "drawing",
          "source_page": "1",
          "source_ref": "door-mark",
          "confidence": 0.7,
          "review_status": "reviewed"
        },
        {
          "opening_id": "window-001",
          "name": "Window",
          "width": 1.2,
          "height": 1.0,
          "count": 2,
          "dimension_unit": "m"
        }
      ]
    }
  ]
}
```

## Output And Engine Handoff

The stable conversion entry point is:

```python
convert_manual_plan_to_aufmass(input_data: ManualAufmassInput) -> AufmassInput
```

The stable calculation entry point is:

```python
calculate_manual_plan_aufmass(input_data: ManualAufmassInput) -> AufmassResult
```

`calculate_manual_plan_aufmass` calls `convert_manual_plan_to_aufmass`, then passes the metric geometry to `calculate_aufmass`. The output unit remains `m`.

## Validation Rules

- Calibration `real_length_m` must be greater than 0.
- Calibration points must not be identical.
- Drawing coordinate values must be finite numbers.
- Each room polygon must contain at least 3 points.
- Room `height_m` must be greater than 0.
- Opening width and height must be non-negative.
- Opening `dimension_unit` must be `drawing` or `m`.
- Invalid input raises `ValueError` with deterministic messages.

## Limitations

This stage does not infer walls, classify openings, detect scale bars, reconcile plan revisions, or validate construction semantics. It assumes the operator already marked the relevant coordinates and entered room heights.

Stage 1 does not read PDF files, images, scans, DXF, DWG, or IFC. It does not add OCR, PDF parsing, image processing, CAD parsing, IFC parsing, file uploads, network calls, subprocess calls, runtime automation, or real construction files. It only converts already-marked coordinates.

## Privacy Boundary

The adapter contains only public-safe conversion logic and synthetic examples. Do not commit real drawings, customer identifiers, addresses, private quantities, contracts, invoices, photos, or source files.

Private pilots with real PDFs or scans must run outside the public repo unless the operator explicitly approves a sanitized public-safe sample.
