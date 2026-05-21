# Aufmass Room Review Table

## Purpose

`core/aufmass_room_review.py` creates the stage 1 in-memory review table between DXF room match candidates and any later Aufmass engine handoff.

The table is for operator review. It does not produce engine input, official quantities, billable quantities, files, uploads, or private construction records.

## Input And Output

The converter accepts a `RoomMatchResult` already returned by `core/aufmass_room_matcher.py`:

```python
room_matches_to_review_table(room_matches) -> AufmassRoomReviewTable
room_review_table_to_rows(table) -> list[dict[str, object]]
room_review_table_to_dict(table) -> dict[str, object]
```

The JSON-compatible dictionary is described by `schemas/aufmass_room_review.schema.json`. Spreadsheet rows use the fixed column order exposed as `ROOM_REVIEW_COLUMNS`.

## Review Columns

Rows preserve the room match reference, contour candidate, match review status, room label context, parsed area label value, calculated area value, area delta, and matcher review notes.

Stage 1 rows include these spreadsheet-oriented fields:

1. `room_id`
2. `room_name`
3. `source_ref`
4. `source_layer`
5. `contour_id`
6. `contour_status`
7. `label_text`
8. `label_status`
9. `area_from_label_m2`
10. `area_calculated_m2`
11. `area_delta_m2`
12. `area_delta_percent`
13. `height_m`
14. `height_source`
15. `openings_status`
16. `review_status`
17. `operator_note`
18. `export_ready`

`room_id` and `source_ref` start from the deterministic matcher `match_id`. `source_layer` is the matched contour layer. `contour_status` remains `candidate`. `label_status` records whether a room label was linked.

The `area_delta_percent` denominator is the parsed label area. It remains `null` when the label area is missing or zero.

`height_m`, `height_source`, and opening review fields reserve review-table space for later operator work. Stage 1 does not infer heights or openings, so height fields start empty and `openings_status` is `not_reviewed`.

`operator_note` starts with deterministic matcher review notes when they exist. That keeps mismatch and missing-label review context visible in a spreadsheet before any later reviewed editing route exists.

## Review Gate

Every row has `export_ready` set to `false`. The table never marks a DXF room candidate ready for engine use by itself.

The summary also carries `official_quantities: false`. The calculated area and area-label comparison are review signals from synthetic or sanitized candidate data, not approvals for a report, invoice, contract, or engine input.

## Boundaries

The module accepts only in-memory matcher output. It does not:

- Read DXF, PDF, DWG, image, scan, OCR, IFC, or spreadsheet files.
- Write files.
- Call network services.
- Call subprocesses.
- Convert drawing units.
- Create official or billable quantities.

Only public-safe synthetic fixtures belong in this repository. Do not commit real drawings, customer identifiers, addresses, private quantities, photos, invoices, contracts, or site records.
