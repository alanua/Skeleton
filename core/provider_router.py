from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.provider_policy import PolicyDecision, RouteStatus, TaskRouteRequest, decide_route
from core.provider_registry import (
    Capability,
    CostClass,
    LatencyClass,
    PrivacyClass,
    ProviderHealth,
    ProviderRoute,
    RouteClass,
    TaskClass,
    default_provider_registry,
)


class ProviderRouterError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RouteReceipt:
    status: RouteStatus
    route_class: RouteClass | None
    provider_alias: str | None
    model_alias: str | None
    reason_code: str
    latency_class: LatencyClass
    cost_class: CostClass
    considered_route_classes: tuple[RouteClass, ...]

    def public_safe(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "route_class": self.route_class.value if self.route_class else None,
            "provider_alias": self.provider_alias,
            "model_alias": self.model_alias,
            "reason_code": self.reason_code,
            "latency_class": self.latency_class.value,
            "cost_class": self.cost_class.value,
            "considered_route_classes": [route_class.value for route_class in self.considered_route_classes],
        }


def request_from_mapping(raw: Mapping[str, object]) -> TaskRouteRequest:
    try:
        provider_health_raw = raw.get("provider_health", {})
        if not isinstance(provider_health_raw, Mapping):
            raise ProviderRouterError("provider_health_mapping_required")
        provider_health = {
            str(provider): ProviderHealth(str(health))
            for provider, health in provider_health_raw.items()
        }
        return TaskRouteRequest(
            task_class=TaskClass(str(raw["task_class"])),
            required_capability=Capability(str(raw["required_capability"])),
            privacy_class=PrivacyClass(str(raw["privacy_class"])),
            max_cost_class=CostClass(str(raw.get("max_cost_class", CostClass.HIGH.value))),
            max_latency_class=LatencyClass(str(raw.get("max_latency_class", LatencyClass.HIGH.value))),
            min_capability_score=float(raw.get("min_capability_score", 0.0)),
            prefer_local=bool(raw.get("prefer_local", True)),
            provider_health=provider_health,
            task_text=str(raw.get("task_text", "")),
        )
    except KeyError as exc:
        raise ProviderRouterError("route_request_missing_required_field") from exc
    except ValueError as exc:
        raise ProviderRouterError("invalid_route_request_enum") from exc


def _receipt_from_decision(decision: PolicyDecision) -> RouteReceipt:
    route = decision.route
    return RouteReceipt(
        status=decision.status,
        route_class=route.route_class if route else None,
        provider_alias=route.provider_alias if route else None,
        model_alias=route.model_alias if route else None,
        reason_code=decision.reason_code,
        latency_class=route.latency_class if route else LatencyClass.NONE,
        cost_class=route.cost_class if route else CostClass.NONE,
        considered_route_classes=decision.considered_route_classes,
    )


def route_task(
    request: TaskRouteRequest,
    registry: tuple[ProviderRoute, ...] | None = None,
) -> RouteReceipt:
    roster = registry if registry is not None else default_provider_registry()
    return _receipt_from_decision(decide_route(roster, request))


def route_task_mapping(
    raw: Mapping[str, object],
    registry: tuple[ProviderRoute, ...] | None = None,
) -> dict[str, object]:
    return route_task(request_from_mapping(raw), registry).public_safe()
