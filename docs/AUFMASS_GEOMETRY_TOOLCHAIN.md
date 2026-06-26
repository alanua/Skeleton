# Aufmass Geometry Toolchain

`core.aufmass_geometry` is the v1 deterministic core2d foundation for public
room-shell geometry. It accepts JSON input, keeps source entities immutable, and
returns JSON-only contracts suitable for higher layers.

## Scope

- `ezdxf` is used only at DXF extraction boundaries.
- Shapely/GEOS is the canonical 2D geometry engine.
- Shapely `STRtree` is the only spatial index.
- NetworkX stores physical-room to functional-zone relationships.
- NumPy handles coordinate transforms and deterministic arithmetic.

The v1 implementation deliberately excludes mesh, BRep, IFC, point-cloud, and
alternate spatial-index backends.

## Commands

```bash
python3 -m core.aufmass_geometry --capabilities
python3 -m core.aufmass_geometry tests/fixtures/aufmass_synthetic/seitenfluegel_room.json
python3 scripts/aufmass_geometry_healthcheck.py
python3 scripts/aufmass_geometry_benchmark.py --synthetic
```

## Review Rules

Endpoint gaps are bridged only within the explicit tolerance. Near-collinear
vertices are removed when topology remains valid. Repairs that fail to produce a
closed shell or materially change quantity evidence return `REVIEW_REQUIRED`.
