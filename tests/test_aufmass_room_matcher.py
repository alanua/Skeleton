from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.aufmass_dxf_adapter import DxfExtractResult, DxfPoint, DxfPolyline, DxfText
from core.aufmass_room_matcher import match_dxf_rooms, parse_area_label, room_match_result_to_dict


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "aufmass_room_match.schema.json"


def test_matches_closed_polylines_to_nearest_room_and_area_labels_from_dict() -> None:
    result = match_dxf_rooms(_dict_fixture())

    assert len(result.contours) == 2
    assert result.contours[0].area == pytest.approx(12.0)
    assert result.contours[0].centroid.x == pytest.approx(2.0)
    assert result.contours[0].centroid.y == pytest.approx(1.5)
    assert result.contours[0].bbox == {"min_x": 0.0, "min_y": 0.0, "max_x": 4.0, "max_y": 3.0}

    first_match = result.matches[0]
    assert first_match.status == "candidate"
    assert first_match.room_label_text == "Office 101"
    assert first_match.area_label_text == "NGF: 12,00 m2"
    assert first_match.parsed_area == pytest.approx(12.0)
    assert first_match.area_delta == pytest.approx(0.0)

    second_match = result.matches[1]
    assert second_match.status == "candidate"
    assert second_match.room_label_text == "Storage"
    assert second_match.area_label_text == "NGF 4.00 m²"


def test_open_polylines_are_not_room_contour_candidates() -> None:
    result = match_dxf_rooms(_dict_fixture())

    assert [contour.source_index for contour in result.contours] == [0, 2]


def test_area_mismatch_status_preserves_delta() -> None:
    fixture = _dict_fixture()
    fixture["mtexts"][0]["text"] = "NGF 10.00 m2"

    match = match_dxf_rooms(fixture).matches[0]

    assert match.status == "area_mismatch"
    assert match.parsed_area == pytest.approx(10.0)
    assert match.area_delta == pytest.approx(2.0)
    assert "Calculated polyline area differs from parsed label area." in match.review_notes


def test_missing_labels_need_review() -> None:
    fixture = _dict_fixture()
    fixture["texts"] = []
    fixture["mtexts"] = []

    match = match_dxf_rooms(fixture).matches[0]

    assert match.status == "needs_review"
    assert match.room_label_id is None
    assert match.area_label_id is None


def test_contour_without_assigned_labels_does_not_borrow_from_sibling_contour() -> None:
    fixture = _dict_fixture()
    fixture["texts"] = [{"layer": "ROOM_NAMES", "text": "Office 101", "insert": {"x": 1, "y": 1, "z": 0}}]
    fixture["mtexts"] = [{"layer": "AREAS", "text": "NGF: 12.00 m2", "insert": {"x": 3, "y": 1, "z": 0}}]

    first_match, second_match = match_dxf_rooms(fixture).matches

    assert first_match.status == "candidate"
    assert first_match.room_label_text == "Office 101"
    assert first_match.area_label_text == "NGF: 12.00 m2"
    assert second_match.status == "needs_review"
    assert second_match.room_label_id is None
    assert second_match.area_label_id is None
    assert second_match.room_label_text is None
    assert second_match.area_label_text is None


def test_outside_label_links_only_to_unique_nearest_contour_within_bounded_tolerance() -> None:
    fixture = _dict_fixture()
    fixture["texts"] = [
        {"layer": "ROOM_NAMES", "text": "Near Office", "insert": {"x": 4.25, "y": 1, "z": 0}},
        {"layer": "ROOM_NAMES", "text": "Too Far", "insert": {"x": 7, "y": 1, "z": 0}},
    ]
    fixture["mtexts"] = []

    first_match, second_match = match_dxf_rooms(fixture).matches

    assert first_match.room_label_text == "Near Office"
    assert first_match.status == "candidate"
    assert "No parseable area label" in " ".join(first_match.review_notes)
    assert second_match.room_label_id is None
    assert second_match.status == "needs_review"
    assert "Too Far" not in (second_match.room_label_text or "")


def test_outside_label_tied_between_contours_is_left_unassigned_with_review_context() -> None:
    fixture = _dict_fixture()
    fixture["polylines"][2]["points"] = [  # type: ignore[index]
        {"x": 5, "y": 0, "z": 0},
        {"x": 7, "y": 0, "z": 0},
        {"x": 7, "y": 2, "z": 0},
        {"x": 5, "y": 2, "z": 0},
    ]
    fixture["texts"] = [{"layer": "ROOM_NAMES", "text": "Shared", "insert": {"x": 4.5, "y": 1, "z": 0}}]
    fixture["mtexts"] = []

    first_match, second_match = match_dxf_rooms(fixture).matches

    assert first_match.room_label_id is None
    assert second_match.room_label_id is None
    assert first_match.status == "needs_review"
    assert second_match.status == "needs_review"
    assert "equally near multiple contours" in " ".join(first_match.review_notes)
    assert first_match.review_notes == second_match.review_notes


def test_label_inside_overlapping_contours_is_ambiguous_and_unassigned() -> None:
    fixture = _dict_fixture()
    fixture["polylines"][2]["points"] = [  # type: ignore[index]
        {"x": 2, "y": 1, "z": 0},
        {"x": 5, "y": 1, "z": 0},
        {"x": 5, "y": 4, "z": 0},
        {"x": 2, "y": 4, "z": 0},
    ]
    fixture["texts"] = [{"layer": "ROOM_NAMES", "text": "Overlap", "insert": {"x": 3, "y": 2, "z": 0}}]
    fixture["mtexts"] = []

    first_match, second_match = match_dxf_rooms(fixture).matches

    assert first_match.room_label_id is None
    assert second_match.room_label_id is None
    assert "overlaps multiple contours" in " ".join(first_match.review_notes)
    assert first_match.review_notes == second_match.review_notes


def test_duplicate_exact_label_text_is_distinct_from_parsed_room_identifier_duplication() -> None:
    fixture = _dict_fixture()
    fixture["texts"] = [
        {"layer": "ROOM_NAMES", "text": "Office 101", "insert": {"x": 1, "y": 1, "z": 0}},
        {"layer": "ROOM_NAMES", "text": "office   101", "insert": {"x": 11, "y": 1, "z": 0}},
    ]
    fixture["mtexts"] = [
        {"layer": "AREAS", "text": "NGF: 12.00 m2", "insert": {"x": 3, "y": 1, "z": 0}},
        {"layer": "AREAS", "text": "NGF 4.00 m2", "insert": {"x": 11.5, "y": 1, "z": 0}},
    ]

    first_match, second_match = match_dxf_rooms(fixture).matches

    assert first_match.status == "needs_review"
    assert second_match.status == "needs_review"
    assert "Duplicate exact label text" in " ".join(first_match.review_notes)
    assert "Duplicate parsed room identifier" not in " ".join(first_match.review_notes)


def test_duplicate_parsed_room_identifier_requires_explicit_room_identifier_syntax() -> None:
    fixture = _dict_fixture()
    fixture["texts"] = [
        {"layer": "ROOM_NAMES", "text": "Room 101", "insert": {"x": 1, "y": 1, "z": 0}},
        {"layer": "ROOM_NAMES", "text": "Rm 101", "insert": {"x": 11, "y": 1, "z": 0}},
    ]

    first_match, second_match = match_dxf_rooms(fixture).matches

    assert first_match.status == "needs_review"
    assert second_match.status == "needs_review"
    assert "Duplicate parsed room identifier '101'" in " ".join(first_match.review_notes)


def test_accepts_dxf_adapter_dataclasses_without_requiring_ezdxf() -> None:
    result = match_dxf_rooms(
        DxfExtractResult(
            path="synthetic.dxf",
            units="m",
            insunits=6,
            polylines=[
                DxfPolyline(
                    layer="ROOMS",
                    entity_type="LWPOLYLINE",
                    points=[
                        DxfPoint(0, 0),
                        DxfPoint(5, 0),
                        DxfPoint(5, 2),
                        DxfPoint(0, 2),
                    ],
                    closed=True,
                )
            ],
            texts=[DxfText(layer="ROOM_NAMES", text="Meeting", insert=DxfPoint(1, 1))],
            mtexts=[DxfText(layer="AREAS", text="10.00 m²", insert=DxfPoint(4, 1))],
        )
    )

    assert result.matches[0].status == "candidate"
    assert result.matches[0].calculated_area == pytest.approx(10.0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("NGF 12.34 m²", 12.34),
        ("NGF: 12,34 m2", 12.34),
        ("12.34 m²", 12.34),
        ("Area 12,34 qm", 12.34),
        ("Office 101", None),
    ],
)
def test_parse_area_label(text: str, expected: float | None) -> None:
    parsed = parse_area_label(text)

    if expected is None:
        assert parsed is None
    else:
        assert parsed == pytest.approx(expected)


def test_room_match_result_to_dict_is_json_compatible() -> None:
    payload = room_match_result_to_dict(match_dxf_rooms(_dict_fixture()))

    assert payload["units"] == "m"
    assert payload["summary"]["stage"] == "room_match_candidates"  # type: ignore[index]
    json.dumps(payload)


def test_schema_file_exists_and_contains_expected_top_level_fields() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.aufmass_room_match.schema.json"
    assert schema["required"] == ["units", "insunits", "contours", "labels", "matches", "summary"]
    assert set(schema["properties"]) >= {"units", "insunits", "contours", "labels", "matches", "summary"}


def test_matcher_module_does_not_import_ezdxf() -> None:
    source = (ROOT / "core" / "aufmass_room_matcher.py").read_text(encoding="utf-8")

    assert "ezdxf" not in source


def _dict_fixture() -> dict[str, object]:
    return {
        "path": "synthetic.dxf",
        "units": "m",
        "insunits": 6,
        "polylines": [
            {
                "layer": "ROOMS",
                "entity_type": "LWPOLYLINE",
                "closed": True,
                "points": [
                    {"x": 0, "y": 0, "z": 0},
                    {"x": 4, "y": 0, "z": 0},
                    {"x": 4, "y": 3, "z": 0},
                    {"x": 0, "y": 3, "z": 0},
                ],
            },
            {
                "layer": "ROOMS",
                "entity_type": "POLYLINE",
                "closed": False,
                "points": [
                    {"x": 20, "y": 20, "z": 0},
                    {"x": 22, "y": 20, "z": 0},
                    {"x": 22, "y": 22, "z": 0},
                ],
            },
            {
                "layer": "ROOMS",
                "entity_type": "LWPOLYLINE",
                "closed": True,
                "points": [
                    {"x": 10, "y": 0, "z": 0},
                    {"x": 12, "y": 0, "z": 0},
                    {"x": 12, "y": 2, "z": 0},
                    {"x": 10, "y": 2, "z": 0},
                ],
            },
        ],
        "texts": [
            {"layer": "ROOM_NAMES", "text": "Office 101", "insert": {"x": 1, "y": 1, "z": 0}},
            {"layer": "ROOM_NAMES", "text": "Storage", "insert": {"x": 11, "y": 1, "z": 0}},
        ],
        "mtexts": [
            {"layer": "AREAS", "text": "NGF: 12,00 m2", "insert": {"x": 3, "y": 1, "z": 0}},
            {"layer": "AREAS", "text": "NGF 4.00 m²", "insert": {"x": 11.5, "y": 1, "z": 0}},
        ],
    }
