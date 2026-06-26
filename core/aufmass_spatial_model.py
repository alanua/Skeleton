from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.aufmass_geometry_evidence import build_room_geometry_evidence


FOUNDATION_STATUSES = {
    "source_candidate",
    "fast_estimate_accepted",
    "estimated_repair",
    "dxf_crosschecked",
    "needs_scale",
    "needs_room_review",
    "needs_zone_review",
    "material_source_conflict",
    "full_resolver_required",
    "failed_safe",
}

WHOLE_FILE_DXF_KEYS = {
    "path",
    "file_name",
    "filename",
    "polylines",
    "texts",
    "mtexts",
    "inserts",
    "blocks",
    "entities",
}


def build_aufmass_foundation(
    plan_candidate: dict[str, Any],
    *,
    dxf_evidence: dict[str, Any] | None = None,
    tolerance_mm: float = 5.0,
) -> dict[str, Any]:
    """Build PDF/image-first apartment, room and zone evidence for one room.

    Room discovery is completed from the calibrated plan candidate before this
    function accepts any DXF evidence. DXF input is selected-room evidence only.
    """
    source = deepcopy(plan_candidate)
    building_part = _building_part(source.get("building_part_profile"))
    if not _calibration_confirmed(source.get("calibration")):
        return _failed_foundation("needs_scale", building_part, ["calibration_not_confirmed"])

    apartment_boundary = _apartment_boundary(source.get("apartment_boundary"))
    if apartment_boundary is None:
        return _failed_foundation("needs_room_review", building_part, ["apartment_boundary_missing_or_invalid"])
    entrances = source.get("entrances")
    if not isinstance(entrances, list) or not entrances:
        return _failed_foundation("needs_room_review", building_part, ["apartment_entrance_missing"])

    rooms = source.get("physical_rooms")
    if not isinstance(rooms, list) or not rooms:
        return _failed_foundation("needs_room_review", building_part, ["physical_rooms_missing"])

    room_id_map: dict[str, str] = {}
    normalized_rooms: list[dict[str, Any]] = []
    normalized_zones: list[dict[str, Any]] = []
    seen_zone_ids: set[str] = set()
    for room_index, room in enumerate(rooms, start=1):
        if not isinstance(room, dict):
            return _failed_foundation("needs_room_review", building_part, ["invalid_room_record"])
        source_room_id = str(room.get("room_id") or f"source-room-{room_index:03d}")
        public_room_id = f"room-{room_index:03d}"
        room_id_map[source_room_id] = public_room_id
        zone_ids: list[str] = []
        zones = room.get("zones") or []
        if not isinstance(zones, list):
            return _failed_foundation("needs_zone_review", building_part, ["invalid_zone_collection"])
        for zone_index, zone in enumerate(zones, start=1):
            if not isinstance(zone, dict):
                return _failed_foundation("needs_zone_review", building_part, ["invalid_zone_record"])
            public_zone_id = f"zone-{len(normalized_zones) + 1:03d}"
            if public_zone_id in seen_zone_ids:
                return _failed_foundation("needs_zone_review", building_part, ["duplicate_zone_id"])
            seen_zone_ids.add(public_zone_id)
            zone_ids.append(public_zone_id)
            normalized_zones.append(
                {
                    "zone_id": public_zone_id,
                    "parent_room_id": public_room_id,
                    "role": _zone_role(zone.get("role")),
                    "color_token": _opaque_color(zone.get("color_token"), zone_index),
                    "boundary_is_semantic_only": True,
                }
            )
        normalized_rooms.append(
            {
                "room_id": public_room_id,
                "room_type": _room_type(room.get("room_type")),
                "zone_ids": zone_ids,
                "boundary_source": "calibrated_pdf_image",
            }
        )

    selected_source_id = str(source.get("selected_room_id") or "")
    selected_public_id = room_id_map.get(selected_source_id)
    if selected_public_id is None:
        return _failed_foundation("needs_room_review", building_part, ["selected_room_not_found"])
    selected_source_room = next(room for room in rooms if str(room.get("room_id") or "") == selected_source_id)
    selected_room_for_geometry = deepcopy(selected_source_room)
    selected_room_for_geometry["room_id"] = selected_public_id
    selected_room_for_geometry["zones"] = [
        {
            "zone_id": zone["zone_id"],
            "role": zone["role"],
        }
        for zone in normalized_zones
        if zone["parent_room_id"] == selected_public_id
    ]

    dxf_crosscheck = _selected_room_dxf_crosscheck(
        selected_source_id,
        selected_public_id,
        dxf_evidence,
    )
    if dxf_crosscheck and dxf_crosscheck["status"] == "failed_safe":
        return _failed_foundation("failed_safe", building_part, [dxf_crosscheck["failure_reason"]])

    geometry = build_room_geometry_evidence(selected_room_for_geometry, tolerance_mm=tolerance_mm)
    graph = _apartment_graph(
        normalized_rooms,
        normalized_zones,
        source.get("connections"),
        room_id_map,
        apartment_boundary,
        entrances,
    )
    room_zone_model = {
        "contract": "ROOM_AND_ZONE_MODEL",
        "physical_rooms": normalized_rooms,
        "functional_zones": normalized_zones,
        "selected_room_id": selected_public_id,
        "label_or_color_splits_room": False,
    }

    status = geometry["status"]
    if dxf_crosscheck:
        if dxf_crosscheck["status"] == "material_source_conflict":
            status = "material_source_conflict"
        elif dxf_crosscheck["status"] == "dxf_crosschecked" and status in {
            "fast_estimate_accepted",
            "estimated_repair",
        }:
            status = "dxf_crosschecked"
        elif dxf_crosscheck["status"] == "needs_room_review":
            status = "needs_room_review"
    if status not in FOUNDATION_STATUSES:
        status = "failed_safe"

    result = {
        "contract": "AUFMASS_FOUNDATION",
        "status": status,
        "building_part_profile": building_part,
        "apartment_graph": graph,
        "room_and_zone_model": room_zone_model,
        "room_geometry_candidate": geometry["room_geometry_candidate"],
        "geometry_evidence": geometry,
        "dxf_crosscheck": dxf_crosscheck,
        "foundation_provenance": {
            "primary_source": "calibrated_pdf_image",
            "dxf_role": "selected_room_crosscheck_only",
            "source_geometry_immutable": True,
        },
        "qa": {
            "physical_room_count": len(normalized_rooms),
            "functional_zone_count": len(normalized_zones),
            "selected_room_count": 1,
            "whole_dxf_enumeration_used": False,
            "unrelated_geometry_ignored": True,
            "quantity_ready": status in {
                "fast_estimate_accepted",
                "estimated_repair",
                "dxf_crosschecked",
            },
        },
    }
    return result


def _selected_room_dxf_crosscheck(
    selected_source_id: str,
    selected_public_id: str,
    evidence: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if evidence is None:
        return None
    if not isinstance(evidence, dict):
        return {
            "contract": "SELECTED_ROOM_DXF_CROSSCHECK",
            "room_id": selected_public_id,
            "status": "failed_safe",
            "failure_reason": "invalid_dxf_evidence",
            "source_scope": "selected_room_only",
            "whole_file_enumeration": False,
            "anchor_count": 0,
            "mapped_segment_count": 0,
        }
    forbidden = sorted(key for key in WHOLE_FILE_DXF_KEYS if key in evidence)
    if forbidden:
        return {
            "contract": "SELECTED_ROOM_DXF_CROSSCHECK",
            "room_id": selected_public_id,
            "status": "failed_safe",
            "failure_reason": "whole_file_dxf_payload_rejected",
            "source_scope": "selected_room_only",
            "whole_file_enumeration": False,
            "anchor_count": 0,
            "mapped_segment_count": 0,
        }
    if str(evidence.get("selected_room_id") or "") != selected_source_id:
        status = "needs_room_review"
        failure_reason = "dxf_selected_room_mismatch"
    elif bool(evidence.get("material_conflict")):
        status = "material_source_conflict"
        failure_reason = "material_pdf_dxf_conflict"
    else:
        aligned = str(evidence.get("alignment_status") or "").lower() in {
            "aligned",
            "matched",
            "crosschecked",
        }
        status = "dxf_crosschecked" if aligned else "needs_room_review"
        failure_reason = None if aligned else "dxf_alignment_not_confirmed"
    anchors = evidence.get("anchors") if isinstance(evidence.get("anchors"), list) else []
    mappings = evidence.get("segment_mappings") if isinstance(evidence.get("segment_mappings"), list) else []
    return {
        "contract": "SELECTED_ROOM_DXF_CROSSCHECK",
        "room_id": selected_public_id,
        "status": status,
        "failure_reason": failure_reason,
        "source_scope": "selected_room_only",
        "whole_file_enumeration": False,
        "anchor_count": len(anchors),
        "mapped_segment_count": len(mappings),
    }


def _apartment_graph(
    rooms: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    connections: Any,
    room_id_map: dict[str, str],
    apartment_boundary: list[list[float]],
    entrances: list[Any],
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [{"id": "apartment", "kind": "APARTMENT"}]
    edges: list[dict[str, Any]] = []
    for room in rooms:
        nodes.append({"id": room["room_id"], "kind": "PHYSICAL_ROOM", "room_type": room["room_type"]})
        edges.append({"source": "apartment", "target": room["room_id"], "relation": "CONTAINS"})
    for zone in zones:
        nodes.append({"id": zone["zone_id"], "kind": "FUNCTIONAL_ZONE", "role": zone["role"]})
        edges.append({"source": zone["parent_room_id"], "target": zone["zone_id"], "relation": "HAS_FUNCTIONAL_ZONE"})
    entrance_ids: list[str] = []
    for index, entrance in enumerate(entrances, start=1):
        if not isinstance(entrance, dict):
            continue
        entrance_id = f"entrance-{index:03d}"
        entrance_ids.append(entrance_id)
        nodes.append({"id": entrance_id, "kind": "ENTRANCE"})
        connected_room = room_id_map.get(str(entrance.get("connected_room_id") or ""))
        edges.append(
            {
                "source": entrance_id,
                "target": connected_room or "apartment",
                "relation": "ENTERS" if connected_room else "BELONGS_TO",
            }
        )
    if isinstance(connections, list):
        for item in connections:
            if not isinstance(item, dict):
                continue
            source = room_id_map.get(str(item.get("from") or ""))
            target = room_id_map.get(str(item.get("to") or ""))
            if source and target:
                edges.append({"source": source, "target": target, "relation": "CONNECTED_TO"})
    return {
        "contract": "APARTMENT_GRAPH",
        "boundary": deepcopy(apartment_boundary),
        "entrance_ids": entrance_ids,
        "nodes": sorted(nodes, key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: (item["source"], item["target"], item["relation"])),
    }


def _failed_foundation(status: str, building_part: str, blockers: list[str]) -> dict[str, Any]:
    return {
        "contract": "AUFMASS_FOUNDATION",
        "status": status,
        "building_part_profile": building_part,
        "apartment_graph": {"contract": "APARTMENT_GRAPH", "boundary": [], "entrance_ids": [], "nodes": [], "edges": []},
        "room_and_zone_model": {
            "contract": "ROOM_AND_ZONE_MODEL",
            "physical_rooms": [],
            "functional_zones": [],
            "selected_room_id": None,
            "label_or_color_splits_room": False,
        },
        "room_geometry_candidate": None,
        "geometry_evidence": None,
        "dxf_crosscheck": None,
        "foundation_provenance": {
            "primary_source": "calibrated_pdf_image",
            "dxf_role": "selected_room_crosscheck_only",
            "source_geometry_immutable": True,
        },
        "qa": {
            "physical_room_count": 0,
            "functional_zone_count": 0,
            "selected_room_count": 0,
            "whole_dxf_enumeration_used": False,
            "unrelated_geometry_ignored": True,
            "quantity_ready": False,
            "blockers": blockers,
        },
    }


def _apartment_boundary(value: Any) -> list[list[float]] | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    boundary: list[list[float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return None
        boundary.append([float(point[0]), float(point[1])])
    return boundary


def _calibration_confirmed(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if str(value.get("status") or "").lower() != "confirmed":
        return False
    evidence = value.get("evidence")
    return isinstance(evidence, list) and bool(evidence)


def _building_part(value: Any) -> str:
    normalized = str(value or "UNKNOWN").upper().replace(" ", "_")
    allowed = {"FRONT_HOUSE", "SIDE_WING", "REAR_HOUSE", "ATTIC", "UNKNOWN"}
    return normalized if normalized in allowed else "UNKNOWN"


def _room_type(value: Any) -> str:
    normalized = str(value or "physical_room").lower()
    allowed = {
        "physical_room",
        "open_physical_room",
        "service_room",
        "technical_room",
        "terrace",
        "stair_void",
    }
    return normalized if normalized in allowed else "physical_room"


def _zone_role(value: Any) -> str:
    normalized = str(value or "open_zone").lower()
    allowed = {
        "open_zone",
        "circulation_zone",
        "service_zone",
        "wet_zone",
        "living_zone",
        "cooking_zone",
    }
    return normalized if normalized in allowed else "open_zone"


def _opaque_color(value: Any, index: int) -> str:
    token = str(value or "").strip()
    if token and len(token) <= 64 and all(character.isalnum() or character in "-_" for character in token):
        return token
    return f"color-{index:03d}"
