# Aufmass Private Pilot Protocol

## Purpose

This protocol defines the public-safe boundary for stage 1 private Aufmass pilots. It allows an operator to run real private drawing work through a private Google Drive project folder or a private local Runner workspace while keeping real project data out of the public Skeleton repository.

This document is a protocol only. It is not a live Drive integration, a file parser, or a runtime bridge.

## Public And Private Boundary

Real drawings and working Aufmass artifacts live in the operator's private Google Drive project folder. The public Skeleton repo must only contain code, schemas, synthetic tests, templates, and public-safe workflow documentation.

The private workspace holds real pilot inputs and outputs, including:

- Real DXF, PDF, image, and scan drawing files.
- Real room tables and manually prepared room lists.
- Real quantities and private extraction or review artifacts.
- Addresses.
- Customer and project identifiers.
- Operator notes that contain private project context.

Private inputs and private outputs stay in the private Drive route or private local workspace. They are not copied into the public repository.

## Allowed Private Inputs

A stage 1 private pilot may read these input types from the private workspace:

- DXF.
- PDF.
- Image or scan.
- Manually prepared room lists.
- Operator notes.

## Public Output Boundary

Forbidden public outputs include:

- Real drawings.
- Real plan screenshots.
- Addresses.
- Customer names.
- Drive URLs.
- Drive file IDs.
- Exact real quantities.
- Private exported tables.

Safe public outputs may include:

- Anonymized bug descriptions.
- Synthetic examples.
- Structural schema improvements.
- Generic edge cases.

## Private Pilot Workflow

1. The operator explicitly activates the private pilot.
2. Private files are read only from the private Drive route or private local workspace.
3. Private extraction and review artifacts are produced only in the private workspace.
4. The operator reviews and corrects the room table privately.
5. Only anonymized lessons or synthetic test cases may be proposed back to public Skeleton.

## Private Room Table Review

Private room tables use these review statuses:

- `needs_review`: extraction or manual preparation exists but the operator has not accepted it.
- `reviewed`: the operator reviewed the room table and recorded corrections needed for private use.
- `rejected`: the room table is not accepted for the pilot output.
- `export_ready`: the private room table is approved for a private export path.

These statuses describe private review state. They do not approve copying a real room table into GitHub.

## GitHub Copy Safety Check

Before copying any pilot lesson, issue note, example, or test proposal into GitHub, confirm that it contains:

- No addresses.
- No customer names.
- No Drive links or file IDs.
- No real plan screenshots.
- No exact private quantities unless the quantity is explicitly anonymized or synthetic.

If the material does not pass every check, keep it in the private workspace.

## Stage 1 Scope

Stage 1 defines the operating protocol and review boundary only. It adds no live Drive behavior, runtime bridge, parser, OCR path, PDF implementation, image implementation, or private project artifact route inside the public repository.
