from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from pathlib import Path
from typing import Any, Optional


MISSING_EZDXF_MESSAGE = "ezdxf is required for DXF extraction; install the aufmass-dxf extra."


@dataclass(frozen=True)
class DxfPoint:
    x: float
    y: float
    z: float = 0.0


@dataclass(frozen=True)
class DxfLine:
    layer: str
    start: DxfPoint
    end: DxfPoint


@dataclass(frozen=True)
class DxfPolyline:
    layer: str
    entity_type: str
    points: list[DxfPoint]
    closed: bool
    area: Optional[float] = None


@dataclass(frozen=True)
class DxfText:
    layer: str
    text: str
    insert: DxfPoint
    height: Optional[float] = None
    rotation: Optional[float] = None


@dataclass(frozen=True)
class DxfDimension:
    layer: str
    dimtype: Optional[int]
    text: Optional[str]
    measurement: Optional[float]
    defpoint: Optional[DxfPoint] = None
    defpoint2: Optional[DxfPoint] = None
    defpoint3: Optional[DxfPoint] = None


@dataclass(frozen=True)
class DxfExtractResult:
    path: str
    units: Optional[str]
    insunits: Optional[int]
    layer_counts: dict[str, int] = field(default_factory=dict)
    entity_counts: dict[str, int] = field(default_factory=dict)
    lines: list[DxfLine] = field(default_factory=list)
    polylines: list[DxfPolyline] = field(default_factory=list)
    texts: list[DxfText] = field(default_factory=list)
    mtexts: list[DxfText] = field(default_factory=list)
    dimensions: list[DxfDimension] = field(default_factory=list)


def extract_dxf(path: str | Path) -> DxfExtractResult:
    """Extract public-safe geometry and annotation metadata from a DXF file."""
    ezdxf = _load_ezdxf()
    dxf_path = Path(path)
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    layer_counts: dict[str, int] = {}
    entity_counts: dict[str, int] = {}
    lines: list[DxfLine] = []
    polylines: list[DxfPolyline] = []
    texts: list[DxfText] = []
    mtexts: list[DxfText] = []
    dimensions: list[DxfDimension] = []

    for entity in msp:
        entity_type = entity.dxftype()
        layer = str(entity.dxf.layer)
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1

        if entity_type == "LINE":
            lines.append(_extract_line(entity))
        elif entity_type == "LWPOLYLINE":
            polylines.append(_extract_lwpolyline(entity))
        elif entity_type == "POLYLINE":
            polylines.append(_extract_polyline(entity))
        elif entity_type == "TEXT":
            texts.append(_extract_text(entity))
        elif entity_type == "MTEXT":
            mtexts.append(_extract_mtext(entity))
        elif entity_type == "DIMENSION":
            dimensions.append(_extract_dimension(entity))

    insunits = _header_int(doc, "$INSUNITS")
    return DxfExtractResult(
        path=str(dxf_path),
        units=_decode_units(ezdxf, insunits),
        insunits=insunits,
        layer_counts=dict(sorted(layer_counts.items())),
        entity_counts=dict(sorted(entity_counts.items())),
        lines=lines,
        polylines=polylines,
        texts=texts,
        mtexts=mtexts,
        dimensions=dimensions,
    )


def extract_closed_polylines(path: str | Path) -> list[DxfPolyline]:
    """Return only closed LWPOLYLINE/POLYLINE entities from a DXF file."""
    return [polyline for polyline in extract_dxf(path).polylines if polyline.closed]


def dxf_result_to_dict(result: DxfExtractResult) -> dict[str, Any]:
    """Convert a DXF extraction result into a JSON-compatible dictionary."""
    return {
        "path": result.path,
        "units": result.units,
        "insunits": result.insunits,
        "layer_counts": result.layer_counts,
        "entity_counts": result.entity_counts,
        "lines": [_line_to_dict(line) for line in result.lines],
        "polylines": [_polyline_to_dict(polyline) for polyline in result.polylines],
        "texts": [_text_to_dict(text) for text in result.texts],
        "mtexts": [_text_to_dict(mtext) for mtext in result.mtexts],
        "dimensions": [_dimension_to_dict(dimension) for dimension in result.dimensions],
    }


def _load_ezdxf() -> Any:
    try:
        return importlib.import_module("ezdxf")
    except ImportError as exc:
        raise RuntimeError(MISSING_EZDXF_MESSAGE) from exc


def _extract_line(entity: Any) -> DxfLine:
    return DxfLine(
        layer=str(entity.dxf.layer),
        start=_point(entity.dxf.start),
        end=_point(entity.dxf.end),
    )


def _extract_lwpolyline(entity: Any) -> DxfPolyline:
    points = [_point(point) for point in entity.get_points("xy")]
    closed = bool(entity.closed)
    return DxfPolyline(
        layer=str(entity.dxf.layer),
        entity_type="LWPOLYLINE",
        points=points,
        closed=closed,
        area=_polygon_area(points) if closed else None,
    )


def _extract_polyline(entity: Any) -> DxfPolyline:
    points = [_point(vertex.dxf.location) for vertex in entity.vertices]
    closed = bool(entity.is_closed)
    return DxfPolyline(
        layer=str(entity.dxf.layer),
        entity_type="POLYLINE",
        points=points,
        closed=closed,
        area=_polygon_area(points) if closed else None,
    )


def _extract_text(entity: Any) -> DxfText:
    return DxfText(
        layer=str(entity.dxf.layer),
        text=str(entity.dxf.text),
        insert=_point(entity.dxf.insert),
        height=_optional_float(entity.dxf.get("height")),
        rotation=_optional_float(entity.dxf.get("rotation")),
    )


def _extract_mtext(entity: Any) -> DxfText:
    return DxfText(
        layer=str(entity.dxf.layer),
        text=entity.plain_text(),
        insert=_point(entity.dxf.insert),
        height=_optional_float(entity.dxf.get("char_height")),
        rotation=_optional_float(entity.dxf.get("rotation")),
    )


def _extract_dimension(entity: Any) -> DxfDimension:
    return DxfDimension(
        layer=str(entity.dxf.layer),
        dimtype=_optional_int(entity.dxf.get("dimtype")),
        text=_optional_string(entity.dxf.get("text")),
        measurement=_dimension_measurement(entity),
        defpoint=_optional_point(entity.dxf.get("defpoint")),
        defpoint2=_optional_point(entity.dxf.get("defpoint2")),
        defpoint3=_optional_point(entity.dxf.get("defpoint3")),
    )


def _dimension_measurement(entity: Any) -> Optional[float]:
    try:
        return _optional_float(entity.get_measurement())
    except (AttributeError, TypeError, ValueError):
        return None


def _point(value: Any) -> DxfPoint:
    return DxfPoint(x=float(value[0]), y=float(value[1]), z=float(value[2]) if len(value) > 2 else 0.0)


def _optional_point(value: Any) -> Optional[DxfPoint]:
    if value is None:
        return None
    return _point(value)


def _polygon_area(points: list[DxfPoint]) -> float:
    if len(points) < 3:
        return 0.0
    doubled_area = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        doubled_area += point.x * next_point.y - next_point.x * point.y
    return abs(doubled_area) / 2.0


def _header_int(doc: Any, name: str) -> Optional[int]:
    value = doc.header.get(name)
    if value is None:
        return None
    return _optional_int(value)


def _decode_units(ezdxf: Any, insunits: Optional[int]) -> Optional[str]:
    if insunits is None:
        return None
    try:
        return str(ezdxf.units.decode(insunits))
    except (AttributeError, KeyError, TypeError, ValueError):
        return str(insunits)


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    return int(value)


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _point_to_dict(point: Optional[DxfPoint]) -> Optional[dict[str, float]]:
    if point is None:
        return None
    return {"x": point.x, "y": point.y, "z": point.z}


def _line_to_dict(line: DxfLine) -> dict[str, Any]:
    return {
        "layer": line.layer,
        "start": _point_to_dict(line.start),
        "end": _point_to_dict(line.end),
    }


def _polyline_to_dict(polyline: DxfPolyline) -> dict[str, Any]:
    return {
        "layer": polyline.layer,
        "entity_type": polyline.entity_type,
        "points": [_point_to_dict(point) for point in polyline.points],
        "closed": polyline.closed,
        "area": polyline.area,
    }


def _text_to_dict(text: DxfText) -> dict[str, Any]:
    return {
        "layer": text.layer,
        "text": text.text,
        "insert": _point_to_dict(text.insert),
        "height": text.height,
        "rotation": text.rotation,
    }


def _dimension_to_dict(dimension: DxfDimension) -> dict[str, Any]:
    return {
        "layer": dimension.layer,
        "dimtype": dimension.dimtype,
        "text": dimension.text,
        "measurement": dimension.measurement,
        "defpoint": _point_to_dict(dimension.defpoint),
        "defpoint2": _point_to_dict(dimension.defpoint2),
        "defpoint3": _point_to_dict(dimension.defpoint3),
    }
