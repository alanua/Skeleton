from __future__ import annotations

from pathlib import Path
import re

PATH = Path("core/aufmass_geometry/facade.py")


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r"def _measure_gap_repair_deltas\(\n"
        r".*?"
        r"\n\ndef _build_candidate\(",
        re.DOTALL,
    )
    replacement = '''def _measure_gap_repair_deltas(
    source_entities: list[dict[str, Any]],
    normalized_segments: list[dict[str, Any]],
) -> dict[str, float] | None:
    ordered = _ordered_closed_segment_chain(normalized_segments)
    if ordered is None:
        return None

    source_by_id = {
        entity["source_entity_id"]: entity
        for entity in source_entities
    }
    before_segments: list[tuple[list[float], list[float]]] = []
    after_segments: list[tuple[list[float], list[float]]] = []
    for segment, forward in ordered:
        source = source_by_id.get(segment["source_entity_id"])
        if source is None:
            return None
        after_segments.append(_oriented_segment_points(segment, forward))
        before_segments.append(_oriented_segment_points(source, forward))

    before = _polygon_from_oriented_segments(before_segments)
    after = _polygon_from_oriented_segments(after_segments)
    if before is None or after is None:
        return None

    original_length = sum(
        float(np.linalg.norm(_array(entity["end"]) - _array(entity["start"])))
        for entity in source_entities
    )
    normalized_length = sum(
        float(np.linalg.norm(_array(segment["end"]) - _array(segment["start"])))
        for segment in normalized_segments
    )
    return {
        "area_delta_m2": abs(float(before.area - after.area)),
        "perimeter_delta_m": abs(float(original_length - normalized_length)),
    }


def _ordered_closed_segment_chain(
    segments: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], bool]] | None:
    if len(segments) < 3:
        return None

    edges: list[
        tuple[dict[str, Any], tuple[float, float], tuple[float, float]]
    ] = []
    adjacency: dict[
        tuple[float, float],
        list[tuple[int, tuple[float, float]]],
    ] = {}
    geometric_edges: set[
        tuple[tuple[float, float], tuple[float, float]]
    ] = set()

    for index, segment in enumerate(segments):
        start = tuple(_point(segment["start"]))
        end = tuple(_point(segment["end"]))
        if start == end:
            return None
        edge_key = (start, end) if start < end else (end, start)
        if edge_key in geometric_edges:
            return None
        geometric_edges.add(edge_key)
        edges.append((segment, start, end))
        adjacency.setdefault(start, []).append((index, end))
        adjacency.setdefault(end, []).append((index, start))

    if any(len(neighbours) != 2 for neighbours in adjacency.values()):
        return None

    start_point = min(adjacency)
    current = start_point
    unused = set(range(len(edges)))
    ordered: list[tuple[dict[str, Any], bool]] = []
    while unused:
        candidates = sorted(
            (neighbour, edge_index)
            for edge_index, neighbour in adjacency[current]
            if edge_index in unused
        )
        if not candidates:
            return None
        neighbour, edge_index = candidates[0]
        segment, edge_start, edge_end = edges[edge_index]
        forward = edge_start == current and edge_end == neighbour
        ordered.append((segment, forward))
        unused.remove(edge_index)
        current = neighbour

    if current != start_point:
        return None
    return ordered


def _oriented_segment_points(
    segment: dict[str, Any],
    forward: bool,
) -> tuple[list[float], list[float]]:
    start = _point(segment["start"])
    end = _point(segment["end"])
    return (start, end) if forward else (end, start)


def _polygon_from_oriented_segments(
    oriented_segments: list[tuple[list[float], list[float]]],
) -> Polygon | None:
    coordinates: list[list[float]] = []
    for start, end in oriented_segments:
        if not coordinates:
            coordinates.append(start)
        elif coordinates[-1] != start:
            coordinates.append(start)
        coordinates.append(end)

    unique_points = {tuple(point) for point in coordinates}
    if len(unique_points) < 3:
        return None
    polygon = Polygon(coordinates)
    if polygon.area <= 0 or not polygon.is_valid:
        return None
    return polygon


def _build_candidate('''
    updated, count = pattern.subn(lambda _: replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"delta measurement block: expected 1 match, got {count}")
    PATH.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
