# Aufmass Source Pack Manifest

## Purpose

The source pack manifest is the first intake checkpoint for Aufmass source references. It records public-safe or private-pilot metadata before any parser, OCR step, or quantity workflow touches a source.

This route only validates declared metadata. It does not open, parse, OCR, copy, or export source files.

## Privacy Boundary

Real source files stay private. Real construction drawings, scans, models, customer material, addresses, private quantities, and pilot outputs must remain in approved private routes such as a private Drive folder or a private Runner workspace.

The public repository may contain only:

- The manifest schema.
- The validator code.
- Synthetic tests and examples.
- Public-safe workflow documentation.

Manifest entries must use opaque artifact references such as `private-ref-alpha` or `synthetic-plan-a`. They must not contain file paths, Drive URLs, file IDs, folder IDs, addresses, customer names, or exact private quantities.

## Required Intake Fields

Each source reference declares:

- Source metadata: `title`, `source_revision`, and `prepared_by`.
- `source_type`: one of the supported public-safe source categories.
- `artifact_route`: the approved public synthetic or private route class.
- `artifact_ref`: an opaque reference token.
- `scale_hint`: a calibration or scale basis before geometry can be trusted.
- `privacy_status`: whether the entry is synthetic, public-safe, private-pilot, or blocked for public use.
- `review_status`: whether the source is draft, needs review, reviewed, rejected, or approved for private intake.

Geometric source types require a meaningful scale or calibration hint. Unknown scale is allowed as a warning so an operator can stage an intake packet, but it is not treated as approved measurement context.

## Current Limits

This task adds no real source files, no OCR, no PDF, DXF, IFC, or image parser changes, no server/runtime route, and no private artifact storage. It is an intake validation layer only.
