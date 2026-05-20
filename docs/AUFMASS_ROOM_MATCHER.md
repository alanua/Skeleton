# Aufmass Room Matcher

## Purpose

`core/aufmass_room_matcher.py` is the first deterministic matching step after DXF extraction.

It links closed DXF polylines with nearby `TEXT` and `MTEXT` labels and returns neutral room-contour match candidates. The output is review material only. It does not claim final, official, or billable room quantities.

## Input

The matcher accepts either:

- `DxfExtractResult` and related dataclasses from `core/aufmass_dxf_adapter.py`.
- A JSON-compatible dictionary shaped like `schemas/aufmass_dxf_extract.schema.json`.

The matcher does not read drawing files and does not import the DXF parser dependency. It works only from already extracted geometry and annotation metadata.

## Output

```python
match_dxf_rooms(dxf_result) -> RoomMatchResult
room_match_result_to_dict(result) -> dict[str, object]
```

The JSON-compatible output is described by `schemas/aufmass_room_match.schema.json`.

The result contains:

- Closed polyline contour candidates with points, centroid, bounding box, layer, source index, and calculated area.
- Text label candidates with source type, layer, insert point, and parsed area when available.
- Match candidates linking each contour to the nearest likely room label and optional area label.
- Status and review fields: `candidate`, `needs_review`, or `area_mismatch`.

## Matching Logic

Only closed polylines are considered room contour candidates. Open polylines are ignored.

For each closed polyline, the matcher:

1. Computes polygon area with the shoelace formula.
2. Computes the polygon centroid and bounding box.
3. Ranks all `TEXT` and `MTEXT` labels by geometric distance to the polygon.
4. Selects the nearest non-area label as the likely room label.
5. Selects the nearest parseable area label as the optional area label.
6. Compares calculated polyline area with parsed label area when available.

Labels inside a contour have distance `0.0`. Labels outside a contour are ranked by shortest distance to the polygon edges.

## Area Parsing

The parser handles simple area notation, including:

- `NGF 12.34 m²`
- `NGF: 12,34 m2`
- `12.34 m²`
- `12,34 qm`

Comma decimals are normalized to dot decimals. If no supported area expression is found, `parsed_area` remains `null`.

Area mismatches are flagged when the absolute delta exceeds the larger of `0.05` drawing units squared or `2%` of calculated contour area.

## Limitations

This is a stage 1 matcher. It does not:

- Resolve overlapping rooms, nested contours, or holes.
- Infer room heights, openings, wall assemblies, or finish quantities.
- Convert drawing units.
- Read DXF, DWG, PDF, image, scan, OCR, or IFC sources.
- Decide official quantities without operator review.

The intended next handoff is from reviewed room-contour candidates into explicit `AufmassInput` geometry for `core/aufmass_engine.py`.

## Privacy Boundary

No real customer drawings, addresses, private quantities, photos, contracts, invoices, or site identifiers belong in this repository.

Synthetic tests may create public-safe geometry fixtures. Private pilots with real DXF files must run outside the public repo unless the operator explicitly approves a sanitized public-safe sample.

## Private Pilot Next Step

Run a private pilot with real DXF files outside the public repository. Use the matcher output to review whether layer naming, label placement, and area labels are consistent enough for a controlled DXF-to-engine handoff.

PDF adapters and image/scan helpers remain later work.
