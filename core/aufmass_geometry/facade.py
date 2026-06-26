from __future__ import annotations

import hashlib
import importlib
import json
import platform
from pathlib import Path
from typing import Any

try:  # optional dependency group: aufmass-geometry
    import networkx as nx
    import numpy as np
    from shapely import geos_version_string
    from shapely.geometry import LineString, MultiLineString, Point, Polygon
    from shapely.ops import polygonize, unary_union
    from shapely.strtree import STRtree
except ImportError:  # pragma: no cover - exercised in minimal environments
    nx = None
    np = None
    geos_version_string = "missing"
    LineString = MultiLineString = Point = Polygon = None
    polygonize = unary_union = STRtree = None

from core.aufmass_geometry.contracts import (
    ACCEPTED,
    AREA_REPORT_M2,
    CONTRACTS,
    DEFAULT_TOLERANCE_MM,
    MATERIAL_AREA_DELTA_M2,
    REVIEW_REQUIRED,
    UNIT_M,
)
from core.aufmass_geometry.io_dxf import extract_dxf_source_entities


def build_capability_report() -> dict[str, Any]:
    """Return deterministic public capabilities without leaking backend objects."""
    missing = [
        name
        for name in ("numpy", "ezdxf", "shapely", "networkx")
        if not _module_available(name)
    ]
    return {
        "contract": "GEOMETRY_CAPABILITY_REPORT",
        "engine": "core2d",
        "status": "AVAILABLE" if not missing else "MISSING_DEPENDENCIES",
        "contracts": list(CONTRACTS),
        "backends": {
            "dxf_extraction": "ezdxf",
            "geometry_2d": "shapely-geos",
            "spatial_index": "shapely-strtree",
            "graph": "networkx",
            "arithmetic": "numpy",
        },
        "default_tolerance_mm": DEFAULT_TOLERANCE_MM,
        "area_report_m2": AREA_REPORT_M2,
        "python_version": platform.python_version(),
        "packages": {
            "numpy": _module_version("numpy"),
            "ezdxf": _module_version("ezdxf"),
            "shapely": _module_version("shapely"),
            "networkx": _module_version("networkx"),
        },
        "geos_version": geos_version_string,
        "missing_dependencies": missing,
        "explicitly_not_loaded": [
            "scipy",
            "rtree",
            "mapbox-earcut",
            "trimesh",
            "manifold3d",
            "fast-simplification",
            "embreex",
            "cadquery",
            "OCP",
            "ifcopenshell",
            "open3d",
        ],
    }


def process_geometry_file(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return process_geometry(payload)


def process_geometry(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic room shell manifest from JSON input."""
    _require_geometry_dependencies()
    tolerance_mm = float(payload.get("tolerance_mm", DEFAULT_TOLERANCE_MM))
    unit = str(payload.get("unit", UNIT_M))
    if unit != UNIT_M:
        raise ValueError("aufmass geometry v1 accepts unit 'm' only.")

    source_entities = _source_entities(payload)
    zones = _zones(payload)
    requested_room_id = str(payload.get("room_id", "room-001"))
    normalized, snap_evidence = _normalize_segments(source_entities, tolerance_mm)
    failed_snap = next(
        (
            item
            for item in snap_evidence
            if item.get("review_status") == REVIEW_REQUIRED
            and item.get("method") == "endpoint_gap_bridge"
        ),
        None,
    )
    if failed_snap:
        candidate = _review_candidate(requested_room_id, str(failed_snap["failure_reason"]))
        ordered_segments: list[dict[str, Any]] = []
        simplification_evidence: list[dict[str, Any]] = []
    else:
        candidate, ordered_segments, simplification_evidence = _build_candidate(
            requested_room_id,
            normalized,
            tolerance_mm,
        )
    graph = _build_room_zone_graph(requested_room_id, zones, candidate["status"])
    strtree_evidence = _build_strtree_evidence(ordered_segments)

    result = {
        "contract": "GEOMETRY_RESULT_MANIFEST",
        "engine": "core2d",
        "status": candidate["status"],
        "tolerance_mm": _round(tolerance_mm, 3),
        "unit": unit,
        "source_entities": source_entities,
        "normalized_segments": normalized,
        "simplification_evidence": snap_evidence + simplification_evidence,
        "room_geometry_candidate": candidate,
        "accepted_room_shell": candidate["accepted_room_shell"],
        "ordered_wall_segments": ordered_segments if candidate["status"] == ACCEPTED else [],
        "room_zone_graph": graph,
        "qa": {
            "capability_report_hash": _stable_hash(build_capability_report()),
            "strtree_lookup": strtree_evidence,
            "public_output_sanitized": True,
        },
    }
    result["manifest_hash"] = _stable_hash(result)
    return result


def _source_entities(payload: dict[str, Any]) -> list[dict[str, Any]]:
    source = payload.get("source", {})
    if source.get("kind") == "dxf":
        return extract_dxf_source_entities(source["path"])

    entities: list[dict[str, Any]] = []
    for index, item in enumerate(payload.get("segments", [])):
        entity_id = str(item.get("id", f"source-{index + 1:03d}"))
        entities.append(
            {
                "contract": "SOURCE_ENTITY",
                "source_entity_id": entity_id,
                "entity_type": "LINE",
                "layer": str(item.get("layer", "WALLS")),
                "start": _point(item["start"]),
                "end": _point(item["end"]),
                "immutable": True,
                "provenance": {
                    "source_ref": str(item.get("source_ref", "synthetic")),
                    "handle": str(item.get("handle", entity_id)),
                },
            }
        )
    return sorted(entities, key=lambda entity: entity["source_entity_id"])


def _zones(payload: dict[str, Any]) -> list[dict[str, Any]]:
    zones = payload.get("zones") or [
        {"zone_id": "zone-living", "label": "Wohnen"},
        {"zone_id": "zone-sleeping", "label": "Schlafen"},
    ]
    return sorted(
        [
            {
                "zone_id": str(zone["zone_id"]),
                "label": str(zone["label"]),
                "role": str(zone.get("role", "FUNCTIONAL_ZONE")),
            }
            for zone in zones
        ],
        key=lambda zone: zone["zone_id"],
    )


def _normalize_segments(
    source_entities: list[dict[str, Any]],
    tolerance_mm: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tolerance_m = tolerance_mm / 1000.0
    endpoints: list[tuple[str, str, np.ndarray]] = []
    for entity in source_entities:
        endpoints.append((entity["source_entity_id"], "start", _array(entity["start"])))
        endpoints.append((entity["source_entity_id"], "end", _array(entity["end"])))

    clusters: list[list[tuple[str, str, np.ndarray]]] = []
    for endpoint in endpoints:
        for cluster in clusters:
            if min(float(np.linalg.norm(endpoint[2] - other[2])) for other in cluster) <= tolerance_m:
                cluster.append(endpoint)
                break
        else:
            clusters.append([endpoint])

    snap_points: dict[tuple[str, str], list[float]] = {
        (entity["source_entity_id"], "start"): entity["start"]
        for entity in source_entities
    }
    snap_points.update(
        {
            (entity["source_entity_id"], "end"): entity["end"]
            for entity in source_entities
        }
    )
    evidence: list[dict[str, Any]] = []
    for cluster_index, cluster in enumerate(clusters, start=1):
        target = np.mean([item[2] for item in cluster], axis=0)
        diameter = _cluster_diameter(cluster)
        max_move = max(float(np.linalg.norm(item[2] - target)) for item in cluster)
        cluster_id = f"endpoint-cluster-{cluster_index:03d}"
        if len(cluster) > 1 and (diameter > tolerance_m or max_move > tolerance_m):
            evidence.append(
                {
                    "contract": "SIMPLIFICATION_EVIDENCE",
                    "method": "endpoint_gap_bridge",
                    "affected_segments": sorted({item[0] for item in cluster}),
                    "tolerance_mm": _round(tolerance_mm, 3),
                    "diameter_m": _round(diameter, 6),
                    "max_endpoint_move_m": _round(max_move, 6),
                    "area_delta_m2": None,
                    "perimeter_delta_m": None,
                    "review_status": REVIEW_REQUIRED,
                    "failure_reason": "endpoint_cluster_exceeds_tolerance",
                    "cluster_id": cluster_id,
                }
            )
            continue
        for source_id, endpoint_name, _point_array in cluster:
            snap_points[(source_id, endpoint_name)] = [_round(float(target[0])), _round(float(target[1]))]
        if len(cluster) > 1 and max_move > 1e-9:
            evidence.append(
                {
                    "contract": "SIMPLIFICATION_EVIDENCE",
                    "method": "endpoint_gap_bridge",
                    "affected_segments": sorted({item[0] for item in cluster}),
                    "tolerance_mm": _round(tolerance_mm, 3),
                    "diameter_m": _round(diameter, 6),
                    "max_endpoint_move_m": _round(max_move, 6),
                    "area_delta_m2": None,
                    "perimeter_delta_m": None,
                    "review_status": ACCEPTED,
                    "cluster_id": cluster_id,
                }
            )

    normalized: list[dict[str, Any]] = []
    for entity in source_entities:
        start = snap_points[(entity["source_entity_id"], "start")]
        end = snap_points[(entity["source_entity_id"], "end")]
        length = float(np.linalg.norm(np.array(end) - np.array(start)))
        normalized.append(
            {
                "contract": "NORMALIZED_SEGMENT",
                "segment_id": f"norm-{entity['source_entity_id']}",
                "source_entity_id": entity["source_entity_id"],
                "layer": entity["layer"],
                "start": start,
                "end": end,
                "length_m": _round(length),
                "normalization": {
                    "method": "unit_m_endpoint_snap",
                    "tolerance_mm": _round(tolerance_mm, 3),
                },
            }
        )
    normalized = sorted(normalized, key=lambda segment: segment["segment_id"])
    if any(item.get("failure_reason") == "endpoint_cluster_exceeds_tolerance" for item in evidence):
        return normalized, evidence

    gap_evidence = [item for item in evidence if item["method"] == "endpoint_gap_bridge"]
    if gap_evidence:
        deltas = _measure_gap_repair_deltas(source_entities, normalized)
        if deltas is None:
            for item in gap_evidence:
                item["review_status"] = REVIEW_REQUIRED
                item["failure_reason"] = "unmeasurable_gap_repair_delta"
            return normalized, evidence
        for item in gap_evidence:
            item["area_delta_m2"] = _round(deltas["area_delta_m2"])
            item["perimeter_delta_m"] = _round(deltas["perimeter_delta_m"])

    return normalized, evidence


def _cluster_diameter(cluster: list[tuple[str, str, np.ndarray]]) -> float:
    diameter = 0.0
    for index, item in enumerate(cluster):
        for other in cluster[index + 1 :]:
            diameter = max(diameter, float(np.linalg.norm(item[2] - other[2])))
    return diameter


def _measure_gap_repair_deltas(
    source_entities: list[dict[str, Any]],
    normalized_segments: list[dict[str, Any]],
) -> dict[str, float] | None:
    before = _polygon_from_segment_chain(source_entities, "source_entity_id")
    after = _polygon_from_segment_chain(normalized_segments, "segment_id")
    if before is None or after is None:
        return None
    original_length = sum(
        float(np.linalg.norm(_array(entity["end"]) - _array(entity["start"])))
        for entity in source_entities
    )
    normalized_length = sum(float(segment["length_m"]) for segment in normalized_segments)
    return {
        "area_delta_m2": abs(float(before.area - after.area)),
        "perimeter_delta_m": abs(float(original_length - normalized_length)),
    }


def _polygon_from_segment_chain(
    segments: list[dict[str, Any]],
    id_key: str,
) -> Polygon | None:
    ordered = sorted(segments, key=lambda segment: str(segment[id_key]))
    if len(ordered) < 3:
        return None
    coords = [_point(segment["start"]) for segment in ordered]
    coords.append(_point(ordered[-1]["end"]))
    polygon = Polygon(coords)
    if polygon.area <= 0 or not polygon.is_valid:
        return None
    return polygon


def _build_candidate(
    room_id: str,
    segments: list[dict[str, Any]],
    tolerance_mm: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    line_strings = [LineString([segment["start"], segment["end"]]) for segment in segments]
    polygons = sorted(
        list(polygonize(unary_union(MultiLineString(line_strings)))),
        key=lambda polygon: (-polygon.area, polygon.wkt),
    )
    if not polygons:
        return _review_candidate(room_id, "no_closed_room_shell"), [], []

    original = polygons[0]
    if original.area <= 0 or not original.is_valid:
        return _review_candidate(room_id, "invalid_room_shell"), [], []

    simplified_coords, removed_ids = _remove_near_collinear_vertices(
        list(original.exterior.coords)[:-1],
        tolerance_mm / 1000.0,
    )
    simplified = Polygon(simplified_coords)
    if not simplified.is_valid or simplified.area <= 0:
        return _review_candidate(room_id, "simplification_invalid"), [], []

    area_delta = abs(float(original.area - simplified.area))
    perimeter_delta = abs(float(original.length - simplified.length))
    review_status = ACCEPTED if area_delta < MATERIAL_AREA_DELTA_M2 else REVIEW_REQUIRED
    evidence = []
    if removed_ids:
        evidence.append(
            {
                "contract": "SIMPLIFICATION_EVIDENCE",
                "method": "near_collinear_vertex_removal",
                "affected_segments": removed_ids,
                "tolerance_mm": _round(tolerance_mm, 3),
                "area_delta_m2": _round(area_delta),
                "perimeter_delta_m": _round(perimeter_delta),
                "review_status": review_status,
            }
        )

    shell = _shell_dict(room_id, simplified)
    candidate = {
        "contract": "ROOM_GEOMETRY_CANDIDATE",
        "room_id": room_id,
        "status": review_status,
        "failure_reason": None if review_status == ACCEPTED else "material_quantity_delta",
        "area_m2": _round(simplified.area),
        "area_report_m2": round(simplified.area, 1),
        "perimeter_m": _round(simplified.length),
        "accepted_room_shell": shell if review_status == ACCEPTED else None,
    }
    ordered = _ordered_wall_segments(room_id, simplified, segments) if review_status == ACCEPTED else []
    return candidate, ordered, evidence


def _review_candidate(room_id: str, reason: str) -> dict[str, Any]:
    return {
        "contract": "ROOM_GEOMETRY_CANDIDATE",
        "room_id": room_id,
        "status": REVIEW_REQUIRED,
        "failure_reason": reason,
        "area_m2": 0.0,
        "area_report_m2": 0.0,
        "perimeter_m": 0.0,
        "accepted_room_shell": None,
    }


def _remove_near_collinear_vertices(
    coords: list[tuple[float, float]],
    tolerance_m: float,
) -> tuple[list[list[float]], list[str]]:
    if len(coords) <= 3:
        return [[_round(x), _round(y)] for x, y in coords], []

    kept = coords[:]
    removed: list[str] = []
    changed = True
    while changed and len(kept) > 3:
        changed = False
        for index, point in enumerate(kept):
            previous = kept[index - 1]
            following = kept[(index + 1) % len(kept)]
            distance = Point(point).distance(LineString([previous, following]))
            if distance <= tolerance_m:
                removed.append(f"vertex-{index:03d}")
                del kept[index]
                changed = True
                break
    return [[_round(x), _round(y)] for x, y in kept], removed


def _shell_dict(room_id: str, polygon: Polygon) -> dict[str, Any]:
    exterior = [[_round(x), _round(y)] for x, y in list(polygon.exterior.coords)[:-1]]
    holes = [
        [[_round(x), _round(y)] for x, y in list(interior.coords)[:-1]]
        for interior in polygon.interiors
    ]
    return {
        "contract": "ACCEPTED_ROOM_SHELL",
        "room_id": room_id,
        "shell_id": f"shell-{room_id}",
        "exterior": exterior,
        "holes": holes,
        "area_m2": _round(polygon.area),
        "area_report_m2": round(polygon.area, 1),
        "perimeter_m": _round(polygon.length),
        "orientation": "ccw" if polygon.exterior.is_ccw else "cw",
    }


def _ordered_wall_segments(
    room_id: str,
    polygon: Polygon,
    normalized_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_lookup = {
        tuple(segment["start"] + segment["end"]): segment["segment_id"]
        for segment in normalized_segments
    }
    coords = [[_round(x), _round(y)] for x, y in list(polygon.exterior.coords)[:-1]]
    ordered = []
    for index, start in enumerate(coords):
        end = coords[(index + 1) % len(coords)]
        forward = tuple(start + end)
        reverse = tuple(end + start)
        ordered.append(
            {
                "contract": "ORDERED_WALL_SEGMENT",
                "room_id": room_id,
                "order": index + 1,
                "wall_segment_id": f"wall-{room_id}-{index + 1:03d}",
                "source_segment_id": source_lookup.get(forward) or source_lookup.get(reverse),
                "start": start,
                "end": end,
                "length_m": _round(LineString([start, end]).length),
            }
        )
    return ordered


def _build_room_zone_graph(room_id: str, zones: list[dict[str, Any]], status: str) -> dict[str, Any]:
    graph = nx.DiGraph()
    graph.add_node(room_id, kind="PHYSICAL_ROOM", status=status)
    for zone in zones:
        graph.add_node(zone["zone_id"], kind=zone["role"], label=zone["label"])
        graph.add_edge(room_id, zone["zone_id"], relation="HAS_FUNCTIONAL_ZONE")
    return {
        "contract": "ROOM_ZONE_GRAPH",
        "nodes": [
            {"id": node_id, **graph.nodes[node_id]}
            for node_id in sorted(graph.nodes)
        ],
        "edges": [
            {"source": source, "target": target, **attrs}
            for source, target, attrs in sorted(graph.edges(data=True))
        ],
    }


def _build_strtree_evidence(segments: list[dict[str, Any]]) -> dict[str, Any]:
    if not segments:
        return {"query_point": [0.0, 0.0], "matched_wall_segment_ids": []}
    geometries = [LineString([segment["start"], segment["end"]]) for segment in segments]
    tree = STRtree(geometries)
    query_point = Point(segments[0]["start"])
    matches = tree.query(query_point.buffer(0.001))
    ids = sorted(segments[int(index)]["wall_segment_id"] for index in matches)
    return {"query_point": segments[0]["start"], "matched_wall_segment_ids": ids}


def _point(value: Any) -> list[float]:
    return [_round(float(value[0])), _round(float(value[1]))]


def _array(value: list[float]) -> np.ndarray:
    return np.array([float(value[0]), float(value[1])], dtype=float)


def _round(value: float, ndigits: int = 6) -> float:
    rounded = round(float(value), ndigits)
    return 0.0 if rounded == -0.0 else rounded


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _module_version(name: str) -> str:
    if not _module_available(name):
        return "missing"
    module = importlib.import_module(name)
    return str(getattr(module, "__version__", "unknown"))


def _module_available(name: str) -> bool:
    try:
        importlib.import_module(name)
    except ImportError:
        return False
    return True


def _require_geometry_dependencies() -> None:
    missing = build_capability_report()["missing_dependencies"]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"aufmass-geometry dependencies are required: {joined}")
