# Aufmass Parser Dependencies

## Purpose

This document records the currently approved optional parser dependency route for Aufmass intake preparation. It adds dependency documentation only. It does not add parser implementation, runtime automation, sample drawings, real measurements, or private construction data.

## Optional Dependency Groups

The parser packages are exposed only as optional extras in `pyproject.toml` so the base install remains minimal.

- `aufmass-dxf`: `ezdxf`
- `aufmass-pdf`: `pdfplumber`, `pypdf`
- `aufmass-image`: `pillow`, `opencv-python-headless`, `scikit-image`
- `aufmass-parsers`: combined DXF, PDF, and image/scan packages listed above

## Format Routes

DXF is the primary CAD route because the operator already converts DWG files to DXF outside this repository. No DWG parser and no DWG converter install is included here.

PDF parsing is routed through `pdfplumber` plus `pypdf`. This keeps the current PDF dependency surface limited to the approved packages.

Image and scan helpers are routed through `pillow`, `opencv-python-headless`, and `scikit-image` for future public-safe helper work. These dependencies do not include OCR.

## Explicit Exclusions

The current parser dependency scope intentionally excludes:

- IFC parser support and `ifcopenshell`
- OCR support and `pytesseract`
- PyMuPDF
- DWG parser or DWG converter installation
- OS package installation

## Data Boundaries

No real drawings, private plan files, customer identifiers, site identifiers, real quantities, invoices, contracts, photos, or private construction data belong in the public repository. Private pilots must use real DXF, PDF, scan, or image sources outside the public repo unless the operator explicitly approves a sanitized public-safe sample.
