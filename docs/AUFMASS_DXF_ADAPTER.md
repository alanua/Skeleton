# Aufmass DXF Adapter

## Purpose

`core/aufmass_dxf_adapter.py` is the stage 1 DXF extraction adapter for public-safe Aufmass work.

It reads operator-supplied DXF files with `ezdxf` and extracts neutral metadata: drawing units, layer and entity counts, lines, LWPOLYLINE/POLYLINE vertices and closed state, closed polyline area, TEXT, MTEXT, and simple DIMENSION records where `ezdxf` exposes measurement data.

## Optional Dependency Boundary

`ezdxf` is optional. The adapter does not import it at module import time.

The extraction entry points lazy-load `ezdxf` only when DXF extraction is requested:

```python
extract_dxf(path)
extract_closed_polylines(path)
```

If `ezdxf` is unavailable, extraction raises:

```text
ezdxf is required for DXF extraction; install the aufmass-dxf extra.
```

This keeps system Python tests safe when the dependency exists only in the dedicated Skeleton virtual environment.

## Entry Points

```python
extract_dxf(path) -> DxfExtractResult
```

Reads modelspace entities from a DXF file and returns structured dataclasses.

```python
extract_closed_polylines(path) -> list[DxfPolyline]
```

Returns the closed LWPOLYLINE/POLYLINE records from `extract_dxf`.

```python
dxf_result_to_dict(result) -> dict
```

Converts a `DxfExtractResult` to a JSON-compatible dictionary matching `schemas/aufmass_dxf_extract.schema.json`.

## Extracted Data

- `$INSUNITS` and decoded unit label where available.
- Layer counts by entity layer.
- Entity counts by DXF entity type.
- LINE start and end points.
- LWPOLYLINE and POLYLINE vertices.
- Polyline `closed` state.
- Closed polyline area in drawing units.
- TEXT content, insert point, height, and rotation.
- MTEXT plain text, insert point, height, and rotation.
- DIMENSION layer, type, display text, measurement, and definition points where available.

## Stage 1 Limits

This adapter does not convert DXF output into Aufmass rooms automatically. Closed polyline area is a geometric extraction aid, not a construction quantity approval.

It does not parse DWG, PDF, images, scans, OCR, IFC, or private project data. It does not call subprocesses, use network access, install dependencies, or write output files.

## Test Data Policy

Tests create small synthetic DXF files at runtime in pytest temporary directories. No real drawings or DXF fixtures are committed.

## Privacy Boundary

Do not commit real customer drawings, addresses, quantities, contracts, invoices, photos, or derived private construction data.

Private pilots with real DXF files must run outside the public repo unless the operator explicitly approves a sanitized public-safe sample.
