from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from core.aufmass_geometry import process_geometry


ACCEPTED_ENGINE_STATUS = "ACCEPTED"


def build_room_geometry_evidence(
    room: dict[str, Any],
    *,
    tolerance_mm: float = 5.0,
) -> dict[str, Any]:
    """Run the selected PDF/image room through the shared core2d workbench.

    The caller supplies exactly one already-selected physical room. This helper
    never scans a drawing or discovers rooms from DXF geometry.
    """
    original = deepcopy(room)
    room_id = _required_token(room, "room_id")
    source_segments = [_geometry_segment(item, index) for index, item in enumerate(room.get("boundary_segments", []), start=1)]
    if len(source_segments) < 3:
        return _failed_evidence(room_id, "insufficient_selected_room_segments", original)

    zones = []
    for index, zone in enumerate(room.get("zones", []), start=1):
        zones.append(
            {
                "zone_id": str(zone.get("zone_id") or f"zone-{index:03d}"),
                "label": str(zone.get("role") or "functional_zone"),
                "role": "FUNCTIONAL_ZONE",
            }
        )

    payload = {
        "room_id": room_id,
        "unit": "m",
        "tolerance_mm": float(tolerance_mm),
        "segments": source_segments,
        "zones": zones,
    }
    result = process_geometry(payload)
    engine_candidate = dict(result.get("room_geometry_candidate") or {})
    repair_evidence = [_sanitized_repair(item) for item in result.get("simplification_evidence", [])]
    repair_applied = any(bool(item.get("repair_applied")) for item in repair_evidence)
    engine_accepted = result.get("status") == ACCEPTED_ENGINE_STATUS
    status = (
        "estimated_repair"
        if engine_accepted and repair_applied
        else "fast_estimate_accepted"
        if engine_accepted
        else "full_resolver_required"
    )

    candidate = {
        "contract": "ROOM_GEOMETRY_CANDIDATE",
        "room_id": room_id,
        "status": status,
        "area_m2": engine_candidate.get("area_m2") if engine_accepted else None,
        "area_report_m2": engine_candidate.get("area_report_m2") if engine_accepted else None,
        "perimeter_m": engine_candidate.get("perimeter_m") if engine_accepted else None,
        "accepted_room_shell": engine_candidate.get("accepted_room_shell") if engine_accepted else None,
        "failure_reason": engine_candidate.get("failure_reason"),
    }
    evidence = {
        "contract": "ROOM_GEOMETRY_EVIDENCE",
        "status": status,
        "geometry_source": "calibrated_pdf_image",
        "source_geometry": {
            "immutable": True,
            "segments": deepcopy(source_segments),
        },
        "room_geometry_candidate": candidate,
        "repair_evidence": repair_evidence,
        "engine": {
            "name": "core2d",
            "manifest_hash": result.get("manifest_hash"),
        },
        "qa": {
            "selected_room_only": True,
            "source_unchanged": room == original,
        },
    }
    evidence["evidence_hash"] = _stable_hash(evidence)
    return evidence


def _geometry_segment(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("boundary segment must be an object")
    start = _point(item.get("start"))
    end = _point(item.get("end"))
    return {
        "id": str(item.get("id") or f"segment-{index:03d}"),
        "layer": "PLAN_BOUNDARY",
        "start": start,
        "end": end,
        "source_ref": "calibrated-plan-candidate",
        "handle": str(item.get("id") or f"segment-{index:03d}"),
    }


def _point(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("boundary point must contain exactly two coordinates")
    return [float(value[0]), float(value[1])]


def _sanitized_repair(item: Any) -> dict[str, Any]:
    source = item if isinstance(item, dict) else {}
    allowed = (
        "contract",
        "method",
        "affected_segments",
        "tolerance_mm",
        "area_delta_m2",
        "perimeter_delta_m",
        "review_status",
        "repair_applied",
        "failure_reason",
        "cluster_id",
    )
    return {key: deepcopy(source.get(key)) for key in allowed if key in source}


def _failed_evidence(room_id: str, reason: str, original: dict[str, Any]) -> dict[str, Any]:
    result = {
        "contract": "ROOM_GEOMETRY_EVIDENCE",
        "status": "full_resolver_required",
        "geometry_source": "calibrated_pdf_image",
        "source_geometry": {"immutable": True, "segments": []},
        "room_geometry_candidate": {
            "contract": "ROOM_GEOMETRY_CANDIDATE",
            "room_id": room_id,
            "status": "full_resolver_required",
            "area_m2": None,
            "area_report_m2": None,
            "perimeter_m": None,
            "accepted_room_shell": None,
            "failure_reason": reason,
        },
        "repair_evidence": [],
        "engine": {"name": "core2d", "manifest_hash": None},
        "qa": {"selected_room_only": True, "source_unchanged": bool(original)},
    }
    result["evidence_hash"] = _stable_hash(result)
    return result


def _required_token(value: dict[str, Any], key: str) -> str:
    token = str(value.get(key) or "").strip()
    if not token:
        raise ValueError(f"{key} is required")
    return token


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
