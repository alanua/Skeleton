# Aufmass Section Opening Height Review

## Purpose

This stage 1 route defines public-safe review tables for using drawing sections to check opening heights and wall areas after plan-based room candidates exist.

Sections can expose vertical information that a room contour cannot prove by itself. The route keeps that information in review tables before any later engine handoff.

This stage adds documentation and schemas only. It does not add a section matcher, opening extractor, wall calculator, spreadsheet writer, engine input converter, or private pilot runtime.

## Table Route

The route has three fixed review-table contracts:

1. `schemas/aufmass_section_alignment.schema.json` records whether a section candidate has been aligned with a plan room wall.
2. `schemas/aufmass_opening_height_review.schema.json` records section-backed opening-height checks for aligned openings.
3. `schemas/aufmass_wall_area_review.schema.json` records wall-area checks after wall length, wall height, and opening-area review context are available.

Each table uses `source_units`, `columns`, `rows`, and `summary`. Its `columns` value is fixed so stage 1 rows can be moved into a spreadsheet without silently changing review meaning.

## Section Alignment Table

Section alignment rows carry these columns:

1. `section_id`
2. `section_name`
3. `section_source_ref`
4. `section_source_layer`
5. `room_id`
6. `room_name`
7. `wall_id`
8. `wall_ref`
9. `alignment_basis`
10. `alignment_status`
11. `operator_note`
12. `export_ready`

An alignment row links a section candidate to one plan-side wall context. `alignment_basis` preserves the clue used for that link, such as a label, wall reference, or operator mark. `alignment_status` keeps conflicts and unreviewed candidates visible instead of treating any visual match as proven.

## Opening Height Review Table

Opening-height rows carry these columns:

1. `opening_id`
2. `opening_name`
3. `opening_kind`
4. `room_id`
5. `room_name`
6. `wall_id`
7. `wall_ref`
8. `section_id`
9. `section_source_ref`
10. `opening_width_m`
11. `opening_height_m`
12. `height_source`
13. `section_alignment_status`
14. `review_status`
15. `operator_note`
16. `export_ready`

The width column keeps the reviewed height next to the plan-side dimension needed for a later opening-area check. A section reference or section-derived height is review context, not approval.

## Wall Area Review Table

Wall-area rows carry these columns:

1. `room_id`
2. `room_name`
3. `wall_id`
4. `wall_ref`
5. `section_id`
6. `wall_length_m`
7. `wall_height_m`
8. `height_source`
9. `opening_ids`
10. `gross_wall_area_m2`
11. `opening_area_m2`
12. `net_wall_area_m2`
13. `review_status`
14. `operator_note`
15. `export_ready`

`opening_ids` preserves which reviewed opening candidates contribute to an opening-area check. Wall-area values stay nullable until the inputs needed for review exist.

## Review Gate

Every stage 1 row has `export_ready: false`. Every summary has `official_quantities: false`.

Allowed statuses keep candidate, missing-height, conflict, and review states visible. They do not approve a room, opening, wall, invoice quantity, contract quantity, or engine input.

## Boundaries

The stage 1 tables do not:

- Infer section-to-plan alignment.
- Read DXF, PDF, DWG, image, scan, OCR, IFC, or spreadsheet files.
- Write files, upload artifacts, call network services, or call subprocesses.
- Convert candidate review rows into engine input or export reports.
- Treat section heights or wall areas as official or billable quantities.

Only public-safe synthetic fixtures belong in this repository. Real sections, drawings, room tables, opening lists, quantities, addresses, customer identifiers, photos, invoices, contracts, and site records stay in the approved private route.
