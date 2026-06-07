# Aufmass Private Workspace Contract

## Purpose

This document defines the minimal private workspace contract for bounded Aufmass private pilots. It is public-safe: it describes folder and artifact locations with placeholders only, and it does not contain drawings, private paths, Drive references, screenshots, secrets, runtime configuration, or real quantities.

The folder layout is an operational contract for where private pilot inputs, review artifacts, corrections, exports, and public-safe lessons belong. It is not proof of data safety by itself. Operators still need private access controls, manual review, leak checks, and the source pack validator before any parser, OCR, or extraction work begins.

## Minimal Workspace Tree

```text
<PRIVATE_WORKSPACE_ROOT>/
  <INPUT_SOURCES>/
    source_pack_manifest.json
    manual_room_list.json
    dxf_candidates/
  review/
    room_review_table.json
  corrections/
    operator_corrections.json
  <PRIVATE_EXPORTS>/
  public_safe_lessons/
    lessons.md
```

## Required Locations

- Source pack manifest: `<PRIVATE_WORKSPACE_ROOT>/<INPUT_SOURCES>/source_pack_manifest.json`
- Room review table: `<PRIVATE_WORKSPACE_ROOT>/review/room_review_table.json`
- Operator corrections: `<PRIVATE_WORKSPACE_ROOT>/corrections/operator_corrections.json`
- Private export location: `<PRIVATE_WORKSPACE_ROOT>/<PRIVATE_EXPORTS>/`
- Public-safe lessons location: `<PRIVATE_WORKSPACE_ROOT>/public_safe_lessons/`

## Optional Locations

- Manual room list: `<PRIVATE_WORKSPACE_ROOT>/<INPUT_SOURCES>/manual_room_list.json`
- DXF candidate artifacts: `<PRIVATE_WORKSPACE_ROOT>/<INPUT_SOURCES>/dxf_candidates/`

## Processing Gate

The source pack validator must run before parser, OCR, extraction, matching, review, or export work. A private pilot is not ready for downstream processing until `source_pack_manifest.json` has passed validation for the intended private route.

## Public Boundary

Real drawings stay private. Real quantities stay private. GitHub receives only anonymized lessons or synthetic tests.

The public Skeleton repository may contain this contract, schemas, validators, public-safe workflow notes, anonymized lessons, and synthetic tests. It must not receive real drawings, real review tables, private exports, private source references, Drive URLs, Drive file IDs, private paths, screenshots, secrets, runtime server configuration, or exact quantities from real projects.

## No-Public-Leak Checklist

Before copying any lesson, issue note, test case, schema proposal, or workflow note into GitHub, confirm all of the following:

- No real drawings or screenshots are included.
- No real quantities are included.
- No private workspace paths are included.
- No Drive URLs, Drive file IDs, or folder IDs are included.
- No customer, site, address, contractor, or project identifiers are included.
- No secrets, tokens, credentials, or runtime server configuration are included.
- Any example data is synthetic or anonymized.
- The source pack validator ran before parser, OCR, or extraction work.
