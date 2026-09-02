from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.provider_policy import (
    ProviderPolicyError,
    TaskRouteRequest,
    eligible_routes,
    rank_routes,
    required_profile,
)
from core.provider_registry import (
    Capability,
    CostClass,
    LatencyClass,
    PrivacyClass,
    RouteClass,
    TaskClass,
    default_provider_registry,
)


ROOT = Path(__file__).resolve().parents[1]


def test_route_classes_are_complete() -> None:
    assert {route.route_class for route in default_provider_registry()} == {
        RouteClass.DETERMINISTIC,
        RouteClass.LOCAL_LOW_COST,
        RouteClass.STRONG_CODING,
        RouteClass.SECONDARY_CODING,
        RouteClass.CLOUD_STRONG_REVIEW,
    }


def test_hard_coding_profile_requires_strong_capability() -> None:
    capability, minimum, requires_strong = required_profile(
        TaskClass.HARD_CODING,
        Capability.REPOSITORY_EDIT,
    )

    assert capability == Capability.REPOSITORY_EDIT
    assert minimum == 0.80
    assert requires_strong is True


def test_hard_coding_invalid_capability_is_rejected() -> None:
    with pytest.raises(ProviderPolicyError, match="hard_coding_requires_coding_capability"):
        required_profile(TaskClass.HARD_CODING, Capability.REASONING)


def test_local_private_can_only_eligible_local_routes() -> None:
    request = TaskRouteRequest(
        task_class=TaskClass.EASY_CODING,
        required_capability=Capability.REASONING,
        privacy_class=PrivacyClass.LOCAL_PRIVATE,
        max_cost_class=CostClass.HIGH,
        max_latency_class=LatencyClass.HIGH,
        min_capability_score=0.50,
    )

    assert {route.route_class for route in eligible_routes(default_provider_registry(), request)} == {
        RouteClass.LOCAL_LOW_COST
    }


def test_review_task_uses_strong_review_route_class() -> None:
    request = TaskRouteRequest(
        task_class=TaskClass.REVIEW,
        required_capability=Capability.CODE_REVIEW,
        privacy_class=PrivacyClass.PUBLIC,
        max_cost_class=CostClass.HIGH,
        max_latency_class=LatencyClass.HIGH,
        min_capability_score=0.0,
    )

    assert [route.route_class for route in rank_routes(default_provider_registry(), request)] == [
        RouteClass.CLOUD_STRONG_REVIEW
    ]


def test_schema_files_parse_and_restrict_extra_request_fields() -> None:
    request_schema = json.loads((ROOT / "schemas" / "provider_route_request.schema.json").read_text())
    receipt_schema = json.loads((ROOT / "schemas" / "provider_route_receipt.schema.json").read_text())

    assert request_schema["additionalProperties"] is False
    assert receipt_schema["additionalProperties"] is False
    assert "task_text" not in receipt_schema["properties"]
