from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

_UNIT_SCALE_TO_METRES = {
    1: 0.0254,
    2: 0.3048,
    4: 0.001,
    5: 0.01,
    6: 1.0,
}


def extract_dxf_source_entities(path: str | Path) -> list[dict[str, Any]]:
    """Extract DXF LINE entities, including nested INSERT transforms, as public JSON."""
    import ezdxf

    doc = ezdxf.readfile(path)
    unit_scale = _unit_scale_to_metres(doc)
    matrix = np.array(
        [[unit_scale, 0.0, 0.0], [0.0, unit_scale, 0.0], [0.0, 0.0, 1.0]]
    )
    entities: list[dict[str, Any]] = []
    for index, entity in enumerate(doc.modelspace(), start=1):
        entities.extend(_extract_entity(doc, entity, matrix, f"msp-{index:03d}", []))
    return sorted(entities, key=lambda item: item["source_entity_id"])


def _extract_entity(
    doc: Any,
    entity: Any,
    matrix: np.ndarray,
    prefix: str,
    insert_chain: list[dict[str, str]],
) -> list[dict[str, Any]]:
    entity_type = entity.dxftype()
    if entity_type == "LINE":
        start = _transform_point(matrix, entity.dxf.start)
        end = _transform_point(matrix, entity.dxf.end)
        return [_line_entity(prefix, entity, start, end, insert_chain)]
    if entity_type == "ARC":
        raise ValueError(f"unsupported curved DXF entity ARC handle={entity.dxf.handle}")
    if entity_type == "LWPOLYLINE":
        return _extract_lwpolyline(entity, matrix, prefix, insert_chain)
    if entity_type != "INSERT":
        return []

    block_name = str(entity.dxf.name)
    block = doc.blocks.get(block_name)
    insert_matrix = matrix @ _insert_matrix(entity)
    chain = [
        *insert_chain,
        {"block_name": block_name, "insert_handle": str(entity.dxf.handle)},
    ]
    nested: list[dict[str, Any]] = []
    for index, child in enumerate(block, start=1):
        nested.extend(_extract_entity(doc, child, insert_matrix, f"{prefix}.{index:03d}", chain))
    return nested


def _extract_lwpolyline(
    entity: Any,
    matrix: np.ndarray,
    prefix: str,
    insert_chain: list[dict[str, str]],
) -> list[dict[str, Any]]:
    points = list(entity.get_points("xyb"))
    if any(abs(float(point[2])) > 1e-12 for point in points):
        raise ValueError(f"unsupported bulged DXF LWPOLYLINE handle={entity.dxf.handle}")
    if len(points) < 2:
        return []

    pairs = list(zip(points, points[1:]))
    if bool(getattr(entity, "closed", False)):
        pairs.append((points[-1], points[0]))

    entities: list[dict[str, Any]] = []
    for index, (start_point, end_point) in enumerate(pairs, start=1):
        start = _transform_point(matrix, start_point)
        end = _transform_point(matrix, end_point)
        entities.append(
            _line_entity(
                f"{prefix}.line-{index:03d}",
                entity,
                start,
                end,
                insert_chain,
            )
        )
    return entities


def _line_entity(
    source_entity_id: str,
    entity: Any,
    start: list[float],
    end: list[float],
    insert_chain: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "contract": "SOURCE_ENTITY",
        "source_entity_id": source_entity_id,
        "entity_type": "LINE",
        "layer": str(entity.dxf.layer),
        "start": start,
        "end": end,
        "immutable": True,
        "provenance": {
            "source_ref": "dxf",
            "insert_chain": insert_chain,
            "handle": str(entity.dxf.handle),
        },
    }


def _insert_matrix(entity: Any) -> np.ndarray:
    insert = entity.dxf.insert
    sx = float(entity.dxf.get("xscale", 1.0))
    sy = float(entity.dxf.get("yscale", 1.0))
    rotation = np.deg2rad(float(entity.dxf.get("rotation", 0.0)))
    translation = np.array(
        [
            [1.0, 0.0, float(insert[0])],
            [0.0, 1.0, float(insert[1])],
            [0.0, 0.0, 1.0],
        ]
    )
    rotate = np.array(
        [
            [float(np.cos(rotation)), -float(np.sin(rotation)), 0.0],
            [float(np.sin(rotation)), float(np.cos(rotation)), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    scale = np.array([[sx, 0.0, 0.0], [0.0, sy, 0.0], [0.0, 0.0, 1.0]])
    return translation @ rotate @ scale


def _transform_point(matrix: np.ndarray, point: Any) -> list[float]:
    transformed = matrix @ np.array([float(point[0]), float(point[1]), 1.0])
    return [_round(float(transformed[0])), _round(float(transformed[1]))]


def _unit_scale_to_metres(doc: Any) -> float:
    unit_code = int(getattr(doc, "units", 0) or doc.header.get("$INSUNITS", 0) or 0)
    if unit_code not in _UNIT_SCALE_TO_METRES:
        raise ValueError(f"unsupported or unitless DXF INSUNITS={unit_code}")
    return _UNIT_SCALE_TO_METRES[unit_code]


def _round(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if rounded == -0.0 else rounded
