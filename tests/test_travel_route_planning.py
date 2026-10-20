from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.travel_route_planning import (
    RouteAlternative,
    RouteLeg,
    RouteSourceMetadata,
    TravelRoutePlan,
    TravelRoutePlanningValidationError,
    normalize_route_plan,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "travel_route_plan.schema.json"


def route_plan() -> TravelRoutePlan:
    source = RouteSourceMetadata(
        source_kind="SYNTHETIC",
        source_revision=2,
        generated_at=2_000_000,
        freshness_seconds=3600,
    )
    return TravelRoutePlan.new(
        origin_ref="loc:origin-opaque",
        destination_ref="loc:destination-opaque",
        requested_modes=("RAIL", "WALK"),
        source_metadata=source,
        alternatives=(
            RouteAlternative(
                alternative_ref="route:alt-b",
                total_duration_seconds=2100,
                modes=("WALK", "RAIL"),
                legs=(
                    RouteLeg(
                        leg_ref="leg:1",
                        leg_index=1,
                        mode="RAIL",
                        duration_seconds=1800,
                        source_ref="route-source:synthetic",
                    ),
                    RouteLeg(
                        leg_ref="leg:0",
                        leg_index=0,
                        mode="WALK",
                        duration_seconds=300,
                        source_ref="route-source:synthetic",
                    ),
                ),
            ),
            RouteAlternative(
                alternative_ref="route:alt-a",
                total_duration_seconds=2400,
                modes=("RAIL",),
                legs=(
                    RouteLeg(
                        leg_ref="leg:2",
                        leg_index=0,
                        mode="RAIL",
                        duration_seconds=2400,
                        source_ref="route-source:synthetic",
                    ),
                ),
            ),
        ),
    )


def test_route_plan_normalizes_deterministically() -> None:
    first = route_plan().to_mapping()
    second = normalize_route_plan(json.loads(json.dumps(first)))

    assert first == second
    assert first["schema"] == "skeleton.travel_route_plan.v1"
    assert first["requested_modes"] == ["RAIL", "WALK"]
    assert [item["alternative_ref"] for item in first["alternatives"]] == [
        "route:alt-a",
        "route:alt-b",
    ]
    assert [
        item["leg_ref"] for item in first["alternatives"][1]["legs"]
    ] == ["leg:0", "leg:1"]


def test_schema_validates_normalized_route_plan() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(route_plan().to_mapping())


def test_opaque_refs_only_and_private_fields_fail_closed() -> None:
    value = route_plan().to_mapping()
    rendered = json.dumps(value).casefold()

    assert "google" not in rendered
    assert "address" not in rendered
    assert "credential" not in rendered
    assert "booking" not in rendered

    value["origin_address"] = "123 Private Street"
    with pytest.raises(TravelRoutePlanningValidationError) as exc:
        normalize_route_plan(value)
    assert exc.value.reason_code == "INVALID_TRAVEL_ROUTE_PLAN_FIELDS"

    unsafe = route_plan().to_mapping()
    unsafe["origin_ref"] = "123 Private Street"
    with pytest.raises(TravelRoutePlanningValidationError):
        normalize_route_plan(unsafe)

    provider = route_plan().to_mapping()
    provider["alternatives"][0]["legs"][0]["source_ref"] = "google:route-1"
    with pytest.raises(TravelRoutePlanningValidationError):
        normalize_route_plan(provider)


def test_route_alternatives_legs_modes_durations_and_freshness_are_bounded() -> None:
    with pytest.raises(TravelRoutePlanningValidationError):
        RouteSourceMetadata(
            source_kind="SYNTHETIC",
            source_revision=1,
            generated_at=0,
            freshness_seconds=604801,
        )
    with pytest.raises(TravelRoutePlanningValidationError):
        RouteAlternative(
            alternative_ref="route:bad",
            total_duration_seconds=100,
            modes=("DRIVE",),
            legs=(
                RouteLeg(
                    leg_ref="leg:0",
                    leg_index=0,
                    mode="DRIVE",
                    duration_seconds=99,
                    source_ref="route-source:synthetic",
                ),
            ),
        )
    too_many = tuple(
        RouteAlternative(
            alternative_ref=f"route:alt-{index}",
            total_duration_seconds=60,
            modes=("WALK",),
            legs=(
                RouteLeg(
                    leg_ref=f"leg:{index}",
                    leg_index=0,
                    mode="WALK",
                    duration_seconds=60,
                    source_ref="route-source:synthetic",
                ),
            ),
        )
        for index in range(4)
    )
    with pytest.raises(TravelRoutePlanningValidationError):
        TravelRoutePlan.new(
            origin_ref="loc:a",
            destination_ref="loc:b",
            requested_modes=("WALK",),
            source_metadata=RouteSourceMetadata(
                source_kind="SYNTHETIC",
                source_revision=1,
                generated_at=0,
                freshness_seconds=0,
            ),
            alternatives=too_many,
        )


def test_route_plan_has_no_side_effect_or_provider_imports() -> None:
    source = Path("core/travel_route_planning.py").read_text(
        encoding="utf-8"
    ).casefold()
    forbidden = (
        "googleapiclient",
        "google.oauth",
        "requests",
        "urllib.request",
        "http.client",
        "sqlite3",
        "subprocess",
        "os.system",
    )
    assert not any(token in source for token in forbidden)
