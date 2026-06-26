from __future__ import annotations

from copy import deepcopy

from core import aufmass_geometry_evidence as evidence_module
from core.aufmass_geometry_evidence import build_room_geometry_evidence


def test_selected_room_geometry_uses_shared_workbench_and_preserves_repair_evidence(monkeypatch) -> None:
    captured = {}

    def fake_process_geometry(payload):
        captured.update(payload)
        return {
            "status": "ACCEPTED",
            "manifest_hash": "hash-001",
            "room_geometry_candidate": {
                "area_m2": 12.004,
                "area_report_m2": 12.0,
                "perimeter_m": 14.002,
                "accepted_room_shell": {"contract": "ACCEPTED_ROOM_SHELL"},
                "failure_reason": None,
            },
            "simplification_evidence": [
                {
                    "contract": "SIMPLIFICATION_EVIDENCE",
                    "method": "endpoint_gap_bridge",
                    "affected_segments": ["segment-001", "segment-004"],
                    "tolerance_mm": 5.0,
                    "area_delta_m2": 0.002,
                    "perimeter_delta_m": 0.003,
                    "review_status": "ACCEPTED",
                    "repair_applied": True,
                }
            ],
        }

    monkeypatch.setattr(evidence_module, "process_geometry", fake_process_geometry)
    room = _selected_room()
    original = deepcopy(room)

    result = build_room_geometry_evidence(room)

    assert captured["room_id"] == "room-001"
    assert [item["start"] for item in captured["segments"]] == [item["start"] for item in room["boundary_segments"]]
    assert [item["end"] for item in captured["segments"]] == [item["end"] for item in room["boundary_segments"]]
    assert result["status"] == "estimated_repair"
    assert result["repair_evidence"][0]["method"] == "endpoint_gap_bridge"
    assert result["source_geometry"]["immutable"] is True
    assert room == original


def test_geometry_failure_returns_full_resolver_required(monkeypatch) -> None:
    monkeypatch.setattr(
        evidence_module,
        "process_geometry",
        lambda payload: {
            "status": "REVIEW_REQUIRED",
            "manifest_hash": "hash-002",
            "room_geometry_candidate": {"failure_reason": "no_closed_room_shell"},
            "simplification_evidence": [],
        },
    )

    result = build_room_geometry_evidence(_selected_room())

    assert result["status"] == "full_resolver_required"
    assert result["room_geometry_candidate"]["area_m2"] is None


def _selected_room():
    return {
        "room_id": "room-001",
        "boundary_segments": [
            {"id": "segment-001", "start": [0.0, 0.0], "end": [4.0, 0.0]},
            {"id": "segment-002", "start": [4.0, 0.0], "end": [4.0, 3.0]},
            {"id": "segment-003", "start": [4.0, 3.0], "end": [0.0, 3.0]},
            {"id": "segment-004", "start": [0.0, 3.0], "end": [0.0, 0.0]},
        ],
        "zones": [
            {"zone_id": "zone-001", "role": "circulation_zone"},
            {"zone_id": "zone-002", "role": "cooking_zone"},
            {"zone_id": "zone-003", "role": "living_zone"},
        ],
    }
