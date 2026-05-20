# Construction Takeoff / Aufmass Workflow

## Purpose

This project route describes a public-safe method for construction takeoff / Aufmass from drawings. It is limited to workflow, state, and routing documentation.

The public repo may hold method-level guidance. Real drawings, real project identifiers, measured quantities, invoices, contracts, photos, and customer data must stay out of the public repo unless the operator explicitly approves a public-safe sample.

## Supported Source Types

Potential source types for a private pilot include:

- IFC model files.
- DWG or DXF drawing files.
- PDF plan exports.
- Scans or photographed plan images.
- Other image files when no structured source exists.

Source files remain private, local, or in an approved private Drive route. This stage adds no sample plans and no source files.

## Multi-Source Comparison Strategy

When multiple formats exist, compare them instead of treating one source as automatically authoritative.

- Prefer structured sources such as IFC or CAD layers for geometry where available.
- Use PDFs as published-plan references and cross-check against structured geometry.
- Use scans and images only with explicit scale calibration and lower confidence unless independently confirmed.
- Record which source produced each measurement and whether another source confirms or conflicts with it.
- Preserve confidence per source layer, contour, opening, and calculated quantity.

## Calculation Targets

Future private pilots may calculate:

- Floor area.
- Ceiling area.
- Room perimeter.
- Gross wall area.
- Net wall area after openings.
- Opening area and opening count.
- Room or zone volume.

Scale must be calibrated from known dimensions before calculations are accepted.

## Confidence And Audit Requirements

Each derived quantity should carry enough audit context to be reviewed later:

- Source type and file route.
- Scale basis or calibration dimension.
- Room or zone identifier from the private project context.
- Source confidence per layer or extraction path.
- Conflict notes when IFC, CAD, PDF, scan, or image sources disagree.
- Manual review status before any quantity is used in a report, invoice, or contract workflow.

## Privacy Boundaries

Do not commit:

- Real customer data.
- Real drawings or plan files.
- Private quantities or calculated measurements from real projects.
- Invoices, contracts, photos, or correspondence.
- Customer, site, address, contractor, or project identifiers.

Private source files and pilot outputs must stay outside the public repo unless the operator explicitly approves a sanitized public-safe sample.

## Current Stage

This stage is manifest and workflow only. It adds no parser, OCR, CAD processing, runtime automation, extraction code, sample drawings, or calculated quantities.
