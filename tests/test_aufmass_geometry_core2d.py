from __future__ import annotations

import importlib.util
import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from core.aufmass_geometry import build_capability_report, process_geometry, process_geometry_file
from core.aufmass_geometry.io_dxf import extract_dxf_source_entities


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "aufmass_synthetic" / "seitenfluegel_room.json"
SCHEMA = ROOT / "schemas" / "aufmass_geometry_manifest.schema.json"
pytestmark = pytest.mark.skipif(
    any(importlib.util.find_spec(name) is None for name in ("numpy", "ezdxf", "shapely", "networkx")),
    reason="aufmass-geometry optional dependencies are not installed",
)


def test_imports_and_deterministic_capability_report() -> None:
    first = build_capability_report()
    second = build_capability_report()

    assert first == second
    assert first["contract"] == "GEOMETRY_CAPABILITY_REPORT"
    assert first["backends"]["geometry_2d"] == "shapely-geos"
    assert first["packages"]["numpy"]
    assert first["geos_version"]


def test_acceptance_room_emits_ordered_shell_and_evidence() -> None:
    result = process_geometry_file(FIXTURE)

    assert result["status"] == "ACCEPTED"
    assert result["accepted_room_shell"]["area_report_m2"] == 21.6
    assert len(result["ordered_wall_segments"]) == 6
    assert {item["method"] for item in result["simplification_evidence"]} >= {"endpoint_gap_bridge"}
    assert result["source_entities"][0]["start"] != result["normalized_segments"][0]["start"]


def test_repeated_runs_produce_identical_outputs_and_hashes() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    first = process_geometry(payload)
    second = process_geometry(payload)

    assert first == second
    assert first["manifest_hash"] == second["manifest_hash"]


def test_gap_repair_within_5mm_succeeds() -> None:
    payload = _rectangle_payload(gap_m=0.004)

    result = process_geometry(payload)

    assert result["status"] == "ACCEPTED"
    assert any(item["method"] == "endpoint_gap_bridge" for item in result["simplification_evidence"])


def test_chain_linked_endpoint_cluster_over_tolerance_fails_closed() -> None:
    payload = {
        "unit": "m",
        "room_id": "room-chain",
        "tolerance_mm": 5,
        "segments": [
            {"id": "a", "start": [0, 0], "end": [4, 0]},
            {"id": "b", "start": [4.004, 0], "end": [4.008, 0]},
            {"id": "c", "start": [4.008, 0], "end": [4, 3]},
            {"id": "d", "start": [4, 3], "end": [0, 3]},
            {"id": "e", "start": [0, 3], "end": [0, 0]},
        ],
    }

    result = process_geometry(payload)

    assert result["status"] == "REVIEW_REQUIRED"
    assert result["room_geometry_candidate"]["failure_reason"] == "endpoint_cluster_exceeds_tolerance"
    assert result["accepted_room_shell"] is None
    assert result["ordered_wall_segments"] == []
    evidence = [
        item for item in result["simplification_evidence"]
        if item.get("failure_reason") == "endpoint_cluster_exceeds_tolerance"
    ][0]
    assert evidence["diameter_m"] == 0.008
    assert evidence["max_endpoint_move_m"] == 0.005


def test_gap_repair_records_measured_quantity_deltas() -> None:
    result = process_geometry(_rectangle_payload(gap_m=0.004))

    evidence = [
        item for item in result["simplification_evidence"]
        if item["method"] == "endpoint_gap_bridge"
    ][0]
    assert evidence["area_delta_m2"] == 0.003
    assert evidence["perimeter_delta_m"] > 0.0
    assert evidence["diameter_m"] == 0.004
    assert evidence["max_endpoint_move_m"] == 0.002


def test_gap_above_tolerance_fails_closed() -> None:
    payload = _rectangle_payload(gap_m=0.010)

    result = process_geometry(payload)

    assert result["status"] == "REVIEW_REQUIRED"
    assert result["room_geometry_candidate"]["failure_reason"] == "no_closed_room_shell"


def test_near_collinear_simplification_preserves_topology() -> None:
    payload = {
        "unit": "m",
        "room_id": "room-collinear",
        "tolerance_mm": 5,
        "segments": [
            {"id": "a", "start": [0, 0], "end": [2, 0.002]},
            {"id": "b", "start": [2, 0.002], "end": [4, 0]},
            {"id": "c", "start": [4, 0], "end": [4, 3]},
            {"id": "d", "start": [4, 3], "end": [0, 3]},
            {"id": "e", "start": [0, 3], "end": [0, 0]},
        ],
    }

    result = process_geometry(payload)

    assert result["status"] == "ACCEPTED"
    assert len(result["accepted_room_shell"]["exterior"]) == 4
    assert result["accepted_room_shell"]["area_report_m2"] == 12.0


def test_material_area_change_becomes_review_required() -> None:
    payload = {
        "unit": "m",
        "room_id": "room-material",
        "tolerance_mm": 500,
        "segments": [
            {"id": "a", "start": [0, 0], "end": [2, 0.4]},
            {"id": "b", "start": [2, 0.4], "end": [4, 0]},
            {"id": "c", "start": [4, 0], "end": [4, 3]},
            {"id": "d", "start": [4, 3], "end": [0, 3]},
            {"id": "e", "start": [0, 3], "end": [0, 0]},
        ],
    }

    result = process_geometry(payload)

    assert result["status"] == "REVIEW_REQUIRED"
    assert result["room_geometry_candidate"]["failure_reason"] == "material_quantity_delta"


def test_polygonization_with_holes_produces_stable_area_perimeter() -> None:
    from shapely.geometry import Polygon

    polygon = Polygon(
        [(0, 0), (5, 0), (5, 5), (0, 5)],
        holes=[[(1, 1), (2, 1), (2, 2), (1, 2)]],
    )

    assert round(polygon.area, 6) == 24.0
    assert round(polygon.length, 6) == 24.0


def test_strtree_lookup_is_deterministic() -> None:
    result = process_geometry_file(FIXTURE)

    assert result["qa"]["strtree_lookup"] == process_geometry_file(FIXTURE)["qa"]["strtree_lookup"]
    assert result["qa"]["strtree_lookup"]["matched_wall_segment_ids"]


def test_networkx_preserves_physical_room_versus_functional_zones() -> None:
    result = process_geometry_file(FIXTURE)

    nodes = {node["id"]: node for node in result["room_zone_graph"]["nodes"]}
    assert nodes["seitenfluegel-room-001"]["kind"] == "PHYSICAL_ROOM"
    assert nodes["zone-wohnen"]["kind"] == "FUNCTIONAL_ZONE"
    assert nodes["zone-essen"]["kind"] == "FUNCTIONAL_ZONE"


def test_no_private_looking_strings_or_local_paths_in_public_outputs() -> None:
    encoded = json.dumps(process_geometry_file(FIXTURE), sort_keys=True)

    assert "/home/" not in encoded
    assert str(ROOT) not in encoded
    assert not re.search(r"[A-Za-z]:\\\\", encoded)


def test_schema_file_exists_and_manifest_satisfies_required_contracts() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    result = process_geometry_file(FIXTURE)

    jsonschema.Draft202012Validator(schema).validate(result)
    assert schema["$id"] == "skeleton.aufmass_geometry_manifest.schema.json"
    assert set(schema["required"]) <= set(result)
    assert result["contract"] == schema["properties"]["contract"]["const"]
    assert {item["contract"] for item in result["source_entities"]} == {"SOURCE_ENTITY"}
    assert {item["contract"] for item in result["normalized_segments"]} == {"NORMALIZED_SEGMENT"}
    assert {item["contract"] for item in result["simplification_evidence"]} == {"SIMPLIFICATION_EVIDENCE"}
    assert result["accepted_room_shell"]["contract"] == "ACCEPTED_ROOM_SHELL"
    assert {item["contract"] for item in result["ordered_wall_segments"]} == {"ORDERED_WALL_SEGMENT"}
    json.dumps(result)


def test_cli_is_deterministic_json() -> None:
    command = [sys.executable, "-m", "core.aufmass_geometry", str(FIXTURE)]

    first = subprocess.run(command, check=True, text=True, capture_output=True).stdout
    second = subprocess.run(command, check=True, text=True, capture_output=True).stdout

    assert json.loads(first) == json.loads(second)


def test_dxf_units_nested_block_transforms_and_source_provenance(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    dxf_path = tmp_path / "nested.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 6
    block = doc.blocks.new(name="WALL_BLOCK")
    block.add_line((0, 0), (2, 0), dxfattribs={"layer": "WALLS"})
    doc.modelspace().add_blockref("WALL_BLOCK", (10, 20), dxfattribs={"rotation": 90})
    doc.saveas(dxf_path)

    result = process_geometry(
        {
            "unit": "m",
            "room_id": "dxf-room",
            "source": {"kind": "dxf", "path": str(dxf_path)},
        }
    )

    entity = result["source_entities"][0]
    assert entity["provenance"]["source_ref"] == "dxf"
    assert entity["start"] == [10.0, 20.0]
    assert entity["end"] == [10.0, 22.0]
    assert entity["provenance"]["insert_chain"][0]["block_name"] == "WALL_BLOCK"


def test_dxf_supported_non_metre_units_convert_to_metres(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    dxf_path = tmp_path / "millimetres.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={"layer": "WALLS"})
    doc.saveas(dxf_path)

    entity = extract_dxf_source_entities(dxf_path)[0]

    assert entity["start"] == [0.0, 0.0]
    assert entity["end"] == [1.0, 0.0]


def test_dxf_unitless_and_unsupported_units_fail_closed(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    for unit_code in (0, 3):
        dxf_path = tmp_path / f"units-{unit_code}.dxf"
        doc = ezdxf.new("R2010")
        doc.units = unit_code
        doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "WALLS"})
        doc.saveas(dxf_path)

        with pytest.raises(ValueError, match=f"INSUNITS={unit_code}"):
            extract_dxf_source_entities(dxf_path)


def test_dxf_arc_fails_closed(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    dxf_path = tmp_path / "arc.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 6
    doc.modelspace().add_arc((0, 0), radius=1, start_angle=0, end_angle=90)
    doc.saveas(dxf_path)

    with pytest.raises(ValueError, match="ARC"):
        extract_dxf_source_entities(dxf_path)


def test_dxf_bulged_lwpolyline_fails_closed(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    dxf_path = tmp_path / "bulged.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 6
    doc.modelspace().add_lwpolyline([(0, 0, 0.5), (1, 0, 0.0)], format="xyb")
    doc.saveas(dxf_path)

    with pytest.raises(ValueError, match="bulged"):
        extract_dxf_source_entities(dxf_path)


def test_dxf_repeated_and_nested_inserts_have_distinct_full_provenance(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    dxf_path = tmp_path / "repeated-nested.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 6
    leaf = doc.blocks.new(name="LEAF")
    leaf.add_line((0, 0), (1, 0), dxfattribs={"layer": "WALLS"})
    parent = doc.blocks.new(name="PARENT")
    parent.add_blockref("LEAF", (0, 0))
    doc.modelspace().add_blockref("PARENT", (0, 0))
    doc.modelspace().add_blockref("PARENT", (10, 0))
    doc.saveas(dxf_path)

    entities = extract_dxf_source_entities(dxf_path)

    assert len(entities) == 2
    assert entities[0]["source_entity_id"] != entities[1]["source_entity_id"]
    chains = [entity["provenance"]["insert_chain"] for entity in entities]
    assert chains[0] != chains[1]
    assert [item["block_name"] for item in chains[0]] == ["PARENT", "LEAF"]
    assert all(entity["provenance"]["handle"] for entity in entities)


def test_disposable_validation_dependency_versions_are_exact() -> None:
    assert importlib.metadata.version("numpy") == "2.4.6"
    assert importlib.metadata.version("ezdxf") == "1.4.4"
    assert importlib.metadata.version("shapely") == "2.1.2"
    assert importlib.metadata.version("networkx") == "3.6.1"
    assert importlib.metadata.version("pillow") == "12.2.0"


def _rectangle_payload(gap_m: float) -> dict[str, object]:
    return {
        "unit": "m",
        "room_id": "room-gap",
        "tolerance_mm": 5,
        "segments": [
            {"id": "a", "start": [0, 0], "end": [4, 0]},
            {"id": "b", "start": [4 + gap_m, 0], "end": [4, 3]},
            {"id": "c", "start": [4, 3], "end": [0, 3]},
            {"id": "d", "start": [0, 3], "end": [0, 0]},
        ],
    }
