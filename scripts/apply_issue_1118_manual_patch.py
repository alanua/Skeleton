from __future__ import annotations

from pathlib import Path
import re

FACADE = Path("core/aufmass_geometry/facade.py")
TESTS = Path("tests/test_aufmass_geometry_core2d.py")


def replace_once(text: str, pattern: re.Pattern[str], replacement: str, label: str) -> str:
    updated, count = pattern.subn(lambda _: replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return updated


def patch_facade() -> None:
    text = FACADE.read_text(encoding="utf-8")

    normalization_pattern = re.compile(
        r"    normalized: list\[dict\[str, Any\]\] = \[\]\n"
        r"    for entity in source_entities:\n"
        r".*?"
        r"    return normalized, evidence\n\n\n"
        r"def _cluster_diameter",
        re.DOTALL,
    )
    normalization_replacement = '''    normalized = _normalized_segments_from_snap_points(
        source_entities,
        snap_points,
        tolerance_mm,
    )
    failed_cluster = any(
        item.get("failure_reason") == "endpoint_cluster_exceeds_tolerance"
        for item in evidence
    )
    if failed_cluster:
        normalized = _normalized_segments_from_source(source_entities, tolerance_mm)
        for item in evidence:
            if item.get("method") == "endpoint_gap_bridge":
                item["area_delta_m2"] = 0.0
                item["perimeter_delta_m"] = 0.0
                item["repair_applied"] = False
        return normalized, evidence

    gap_evidence = [item for item in evidence if item["method"] == "endpoint_gap_bridge"]
    if gap_evidence:
        deltas = _measure_gap_repair_deltas(source_entities, normalized)
        if deltas is None:
            normalized = _normalized_segments_from_source(source_entities, tolerance_mm)
            for item in gap_evidence:
                item["area_delta_m2"] = 0.0
                item["perimeter_delta_m"] = 0.0
                item["repair_applied"] = False
                item["review_status"] = REVIEW_REQUIRED
                item["failure_reason"] = "unmeasurable_gap_repair_delta"
            return normalized, evidence
        for item in gap_evidence:
            item["area_delta_m2"] = _round(deltas["area_delta_m2"])
            item["perimeter_delta_m"] = _round(deltas["perimeter_delta_m"])
            item["repair_applied"] = True

    return normalized, evidence


def _normalized_segments_from_source(
    source_entities: list[dict[str, Any]],
    tolerance_mm: float,
) -> list[dict[str, Any]]:
    source_points: dict[tuple[str, str], list[float]] = {}
    for entity in source_entities:
        source_id = entity["source_entity_id"]
        source_points[(source_id, "start")] = _point(entity["start"])
        source_points[(source_id, "end")] = _point(entity["end"])
    return _normalized_segments_from_snap_points(
        source_entities,
        source_points,
        tolerance_mm,
    )


def _normalized_segments_from_snap_points(
    source_entities: list[dict[str, Any]],
    snap_points: dict[tuple[str, str], list[float]],
    tolerance_mm: float,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for entity in source_entities:
        source_id = entity["source_entity_id"]
        start = snap_points[(source_id, "start")]
        end = snap_points[(source_id, "end")]
        length = float(np.linalg.norm(np.array(end) - np.array(start)))
        normalized.append(
            {
                "contract": "NORMALIZED_SEGMENT",
                "segment_id": f"norm-{source_id}",
                "source_entity_id": source_id,
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
    return sorted(normalized, key=lambda segment: segment["segment_id"])


def _cluster_diameter'''
    text = replace_once(
        text,
        normalization_pattern,
        normalization_replacement,
        "normalization block",
    )

    old_length = '    normalized_length = sum(float(segment["length_m"]) for segment in normalized_segments)\n'
    new_length = '''    normalized_length = sum(
        float(np.linalg.norm(_array(segment["end"]) - _array(segment["start"])))
        for segment in normalized_segments
    )
'''
    if text.count(old_length) != 1:
        raise RuntimeError("normalized length expression did not match exactly once")
    text = text.replace(old_length, new_length, 1)

    polygon_pattern = re.compile(
        r"def _polygon_from_segment_chain\(\n"
        r"    segments: list\[dict\[str, Any\]\],\n"
        r"    id_key: str,\n"
        r"\) -> Polygon \| None:\n"
        r".*?"
        r"    return polygon\n",
        re.DOTALL,
    )
    polygon_replacement = '''def _polygon_from_segment_chain(
    segments: list[dict[str, Any]],
    id_key: str,
) -> Polygon | None:
    del id_key
    if len(segments) < 3:
        return None

    raw_edges: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for segment in segments:
        start = tuple(_point(segment["start"]))
        end = tuple(_point(segment["end"]))
        if start == end:
            return None
        raw_edges.append((start, end) if start < end else (end, start))

    edges = sorted(raw_edges)
    if len(set(edges)) != len(edges):
        return None

    adjacency: dict[
        tuple[float, float],
        list[tuple[int, tuple[float, float]]],
    ] = {}
    for edge_index, (start, end) in enumerate(edges):
        adjacency.setdefault(start, []).append((edge_index, end))
        adjacency.setdefault(end, []).append((edge_index, start))

    degrees = {point: len(neighbours) for point, neighbours in adjacency.items()}
    if any(degree not in (1, 2) for degree in degrees.values()):
        return None
    open_ends = sorted(point for point, degree in degrees.items() if degree == 1)
    if len(open_ends) == 2:
        start = open_ends[0]
        expected_end = open_ends[1]
    elif not open_ends and all(degree == 2 for degree in degrees.values()):
        start = min(adjacency)
        expected_end = start
    else:
        return None

    coordinates = [start]
    current = start
    unused = set(range(len(edges)))
    while unused:
        candidates = sorted(
            (neighbour, edge_index)
            for edge_index, neighbour in adjacency[current]
            if edge_index in unused
        )
        if not candidates:
            return None
        neighbour, edge_index = candidates[0]
        unused.remove(edge_index)
        coordinates.append(neighbour)
        current = neighbour

    if current != expected_end:
        return None
    unique_coordinates = (
        coordinates[:-1] if coordinates[-1] == coordinates[0] else coordinates
    )
    if len(set(unique_coordinates)) < 3:
        return None

    polygon = Polygon(coordinates)
    if polygon.area <= 0 or not polygon.is_valid:
        return None
    return polygon
'''
    text = replace_once(text, polygon_pattern, polygon_replacement, "polygon chain helper")
    FACADE.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    text = TESTS.read_text(encoding="utf-8")
    marker = "def test_gap_repair_is_id_and_direction_independent()"
    if marker in text:
        raise RuntimeError("manual regression tests already exist")

    tests = r'''


def test_failure_manifests_remain_schema_valid() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    cluster_failure = {
        "unit": "m",
        "room_id": "room-chain-schema",
        "tolerance_mm": 5,
        "segments": [
            {"id": "a", "start": [0, 0], "end": [4, 0]},
            {"id": "b", "start": [4.004, 0], "end": [4.008, 0]},
            {"id": "c", "start": [4.008, 0], "end": [4, 3]},
            {"id": "d", "start": [4, 3], "end": [0, 3]},
            {"id": "e", "start": [0, 3], "end": [0, 0]},
        ],
    }
    unmeasurable = {
        "unit": "m",
        "room_id": "room-unmeasurable-schema",
        "tolerance_mm": 5,
        "segments": [
            {"id": "a", "start": [0, 0], "end": [1, 0]},
            {"id": "b", "start": [1.004, 0], "end": [2, 0]},
            {"id": "c", "start": [1, 0], "end": [1, 1]},
        ],
    }

    for payload, reason in (
        (cluster_failure, "endpoint_cluster_exceeds_tolerance"),
        (unmeasurable, "unmeasurable_gap_repair_delta"),
    ):
        result = process_geometry(payload)
        assert result["status"] == "REVIEW_REQUIRED"
        assert result["room_geometry_candidate"]["failure_reason"] == reason
        validator.validate(result)
        evidence = next(
            item
            for item in result["simplification_evidence"]
            if item.get("failure_reason") == reason
        )
        assert evidence["area_delta_m2"] == 0.0
        assert evidence["perimeter_delta_m"] == 0.0
        assert evidence["repair_applied"] is False


def test_gap_repair_is_id_and_direction_independent() -> None:
    baseline = process_geometry(_rectangle_payload(gap_m=0.004))
    shuffled_reversed = process_geometry(
        {
            "unit": "m",
            "room_id": "room-gap-shuffled",
            "tolerance_mm": 5,
            "segments": [
                {"id": "z-wall", "start": [0, 3], "end": [4, 3]},
                {"id": "a-wall", "start": [4, 0], "end": [0, 0]},
                {"id": "m-wall", "start": [0, 0], "end": [0, 3]},
                {"id": "b-wall", "start": [4, 3], "end": [4.004, 0]},
            ],
        }
    )

    assert baseline["status"] == "ACCEPTED"
    assert shuffled_reversed["status"] == "ACCEPTED"
    baseline_evidence = next(
        item
        for item in baseline["simplification_evidence"]
        if item["method"] == "endpoint_gap_bridge"
    )
    shuffled_evidence = next(
        item
        for item in shuffled_reversed["simplification_evidence"]
        if item["method"] == "endpoint_gap_bridge"
    )
    assert shuffled_evidence["area_delta_m2"] == baseline_evidence["area_delta_m2"]
    assert shuffled_evidence["perimeter_delta_m"] == baseline_evidence["perimeter_delta_m"]
    assert (
        shuffled_reversed["accepted_room_shell"]["area_m2"]
        == baseline["accepted_room_shell"]["area_m2"]
    )
    assert (
        shuffled_reversed["accepted_room_shell"]["perimeter_m"]
        == baseline["accepted_room_shell"]["perimeter_m"]
    )
'''
    TESTS.write_text(text + tests, encoding="utf-8")


def main() -> None:
    patch_facade()
    patch_tests()


if __name__ == "__main__":
    main()
