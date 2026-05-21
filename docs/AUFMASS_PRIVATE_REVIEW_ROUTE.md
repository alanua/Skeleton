# Aufmass Private Review Route

## Purpose

This stage 1 route defines a public-safe review path for real Aufmass private pilot work. It records the boundary between private operator work and the public Skeleton repository without moving private pilot artifacts into Git.

This is a route, schema, and template definition only. It is not live Drive integration and it is not a parser.

## Private Pilot Route

1. The operator explicitly activates the private pilot.
2. Private source files stay in the operator's private Google Drive project folder or private local workspace.
3. Extraction and review artifacts stay private.
4. The public repository receives only anonymized lessons or synthetic tests.

## Suggested Private Workspace Stages

- `input_sources`
- `extracted_candidates`
- `room_review_table`
- `operator_corrections`
- `private_exports`
- `public_safe_lessons`

These stage names describe private workspace handoffs. Public manifests may name a stage, but they must not carry the underlying private artifact.

## Review Gates

- `source_selected`: the operator selected a private pilot source.
- `extraction_done`: private extraction or manual candidate preparation is complete.
- `room_table_ready`: a private room review table is ready for operator checking.
- `operator_reviewed`: the operator recorded the private review result.
- `export_ready`: the approved private output can move to a private export path.
- `public_synthetic_candidate`: the lesson is reduced to anonymized guidance or a synthetic test candidate.

## Public-Safe Manifest Boundary

`schemas/aufmass_private_pilot_manifest.schema.json` describes a public-safe manifest shape for this route. It may store only:

- An opaque `private_ref`.
- `source_type`.
- `review_stage`.
- `artifact_kind`.
- `public_safety_status`.

The manifest may identify the Aufmass public route and carry public-safe notes, but it must not reveal the private artifact behind an opaque reference.

## Never Store Publicly

Do not store these in the public repository:

- Drive URLs.
- Drive file IDs.
- Folder IDs.
- Exact private file names when they reveal an object, address, or customer.
- Addresses.
- Customer names.
- Plan screenshots.
- Real room tables.
- Exact real quantities.
- Private exported tables.

## Scope Boundary

This route does not add a Drive client, a parser, OCR, extraction runtime, upload path, or private export implementation. Real pilot sources, extracted candidates, review tables, corrections, and exports remain in the private project route.
