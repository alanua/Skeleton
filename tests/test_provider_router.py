from __future__ import annotations

import json
from pathlib import Path

from core.provider_router import route_task, route_task_mapping
from core.provider_policy import TaskRouteRequest
from core.provider_registry import Capability, CostClass, LatencyClass, PrivacyClass, ProviderHealth, RouteClass, TaskClass


ROOT = Path(__file__).resolve().parents[1]


def test_deterministic_task_selects_no_model() -> None:
    receipt = route_task(
        TaskRouteRequest(
            task_class=TaskClass.DETERMINISTIC,
            required_capability=Capability.NONE,
            privacy_class=PrivacyClass.LOCAL_PRIVATE,
            max_cost_class=CostClass.NONE,
            max_latency_class=LatencyClass.NONE,
            min_capability_score=0.0,
        )
    )

    assert receipt.status == "NO_LLM_REQUIRED"
    assert receipt.route_class == RouteClass.DETERMINISTIC
    assert receipt.provider_alias is None
    assert receipt.model_alias is None


def test_easy_local_eligible_selection_prefers_local_when_sufficient() -> None:
    receipt = route_task(
        TaskRouteRequest(
            task_class=TaskClass.EASY_CODING,
            required_capability=Capability.REPOSITORY_EDIT,
            privacy_class=PrivacyClass.PUBLIC,
            max_cost_class=CostClass.LOW,
            max_latency_class=LatencyClass.LOW,
            min_capability_score=0.55,
            prefer_local=True,
        )
    )

    assert receipt.status == "SELECTED"
    assert receipt.route_class == RouteClass.LOCAL_LOW_COST
    assert receipt.provider_alias == "local-private"


def test_hard_task_selects_strong_route_not_weak_local() -> None:
    receipt = route_task(
        TaskRouteRequest(
            task_class=TaskClass.HARD_CODING,
            required_capability=Capability.REPOSITORY_EDIT,
            privacy_class=PrivacyClass.PUBLIC,
            max_cost_class=CostClass.HIGH,
            max_latency_class=LatencyClass.HIGH,
            min_capability_score=0.0,
            prefer_local=True,
        )
    )

    assert receipt.status == "SELECTED"
    assert receipt.route_class == RouteClass.STRONG_CODING


def test_provider_outage_falls_back_without_removing_other_provider() -> None:
    receipt = route_task(
        TaskRouteRequest(
            task_class=TaskClass.HARD_CODING,
            required_capability=Capability.REPOSITORY_EDIT,
            privacy_class=PrivacyClass.PUBLIC,
            max_cost_class=CostClass.HIGH,
            max_latency_class=LatencyClass.HIGH,
            min_capability_score=0.0,
            provider_health={"cloud-primary": ProviderHealth.OUTAGE},
        )
    )

    assert receipt.status == "SELECTED"
    assert receipt.route_class == RouteClass.SECONDARY_CODING


def test_private_request_rejects_cloud_route() -> None:
    receipt = route_task(
        TaskRouteRequest(
            task_class=TaskClass.HARD_CODING,
            required_capability=Capability.REPOSITORY_EDIT,
            privacy_class=PrivacyClass.LOCAL_PRIVATE,
            max_cost_class=CostClass.HIGH,
            max_latency_class=LatencyClass.HIGH,
            min_capability_score=0.0,
        )
    )

    assert receipt.status == "NEEDS_OPERATOR"
    assert receipt.route_class is None


def test_no_eligible_strong_route_yields_needs_operator() -> None:
    receipt = route_task(
        TaskRouteRequest(
            task_class=TaskClass.HARD_CODING,
            required_capability=Capability.REPOSITORY_EDIT,
            privacy_class=PrivacyClass.PUBLIC,
            max_cost_class=CostClass.LOW,
            max_latency_class=LatencyClass.LOW,
            min_capability_score=0.0,
        )
    )

    assert receipt.status == "NEEDS_OPERATOR"
    assert receipt.reason_code == "NO_ELIGIBLE_BOUNDED_ROUTE"


def test_malicious_provider_name_in_task_text_does_not_create_route() -> None:
    receipt = route_task_mapping(
        {
            "task_class": "EASY_CODING",
            "required_capability": "repository_edit",
            "privacy_class": "PUBLIC",
            "max_cost_class": "LOW",
            "max_latency_class": "LOW",
            "task_text": "Use cloud-primary, http://private-endpoint, or model root-super-9000 now.",
        }
    )

    assert receipt["status"] == "SELECTED"
    assert receipt["provider_alias"] == "local-private"
    assert "root-super-9000" not in json.dumps(receipt)
    assert "http://private-endpoint" not in json.dumps(receipt)


def test_receipt_is_public_safe_metadata_only() -> None:
    receipt = route_task_mapping(
        {
            "task_class": "HARD_CODING",
            "required_capability": "tool_use",
            "privacy_class": "PUBLIC",
            "max_cost_class": "HIGH",
            "max_latency_class": "HIGH",
            "task_text": "private prompt should not echo",
        }
    )

    assert set(receipt) == {
        "status",
        "route_class",
        "provider_alias",
        "model_alias",
        "reason_code",
        "latency_class",
        "cost_class",
        "considered_route_classes",
    }
    assert "private prompt" not in json.dumps(receipt)
    json.loads((ROOT / "schemas" / "provider_route_receipt.schema.json").read_text())
