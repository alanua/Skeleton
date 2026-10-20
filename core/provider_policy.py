from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from core.provider_registry import (
    Capability,
    CostClass,
    LatencyClass,
    Locality,
    PrivacyClass,
    ProviderHealth,
    ProviderRoute,
    RouteClass,
    TaskClass,
    provider_health_rank,
)


class ProviderPolicyError(ValueError):
    pass


class RouteStatus(StrEnum):
    SELECTED = "SELECTED"
    NO_LLM_REQUIRED = "NO_LLM_REQUIRED"
    NEEDS_OPERATOR = "NEEDS_OPERATOR"


@dataclass(frozen=True, slots=True)
class TaskRouteRequest:
    task_class: TaskClass
    required_capability: Capability
    privacy_class: PrivacyClass
    max_cost_class: CostClass
    max_latency_class: LatencyClass
    min_capability_score: float
    prefer_local: bool = True
    provider_health: Mapping[str, ProviderHealth] | None = None
    task_text: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.min_capability_score) <= 1.0:
            raise ProviderPolicyError("min_capability_score_out_of_range")
        if self.task_class is TaskClass.DETERMINISTIC and self.required_capability is not Capability.NONE:
            raise ProviderPolicyError("deterministic_task_cannot_require_llm_capability")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    status: RouteStatus
    route: ProviderRoute | None
    reason_code: str
    considered_route_classes: tuple[RouteClass, ...]


def required_profile(task_class: TaskClass, required_capability: Capability) -> tuple[Capability, float, bool]:
    if task_class is TaskClass.DETERMINISTIC:
        return Capability.NONE, 1.0, False
    if task_class is TaskClass.HARD_CODING:
        if required_capability not in {Capability.REPOSITORY_EDIT, Capability.TOOL_USE}:
            raise ProviderPolicyError("hard_coding_requires_coding_capability")
        return required_capability, 0.80, True
    if task_class is TaskClass.REVIEW:
        return Capability.CODE_REVIEW, 0.80, True
    if task_class is TaskClass.EASY_CODING:
        return required_capability, 0.55, False
    return required_capability, 0.60, False


def allowed_route_classes(task_class: TaskClass) -> frozenset[RouteClass]:
    if task_class is TaskClass.DETERMINISTIC:
        return frozenset({RouteClass.DETERMINISTIC})
    if task_class is TaskClass.HARD_CODING:
        return frozenset({RouteClass.STRONG_CODING, RouteClass.SECONDARY_CODING})
    if task_class is TaskClass.REVIEW:
        return frozenset({RouteClass.CLOUD_STRONG_REVIEW})
    return frozenset(
        {
            RouteClass.LOCAL_LOW_COST,
            RouteClass.STRONG_CODING,
            RouteClass.SECONDARY_CODING,
            RouteClass.CLOUD_STRONG_REVIEW,
        }
    )


def _health_for(route: ProviderRoute, request: TaskRouteRequest) -> ProviderHealth:
    if route.provider_alias is None:
        return ProviderHealth.LIVE
    if not request.provider_health:
        return ProviderHealth.LIVE
    return request.provider_health.get(route.provider_alias, ProviderHealth.LIVE)


def eligible_routes(
    registry: tuple[ProviderRoute, ...],
    request: TaskRouteRequest,
) -> tuple[ProviderRoute, ...]:
    capability, minimum, requires_strong = required_profile(
        request.task_class,
        request.required_capability,
    )
    minimum = max(minimum, request.min_capability_score)
    allowed = allowed_route_classes(request.task_class)

    candidates: list[ProviderRoute] = []
    for route in registry:
        if route.route_class not in allowed:
            continue
        if request.task_class is TaskClass.DETERMINISTIC:
            if route.route_class is RouteClass.DETERMINISTIC:
                candidates.append(route)
            continue
        if route.route_class is RouteClass.DETERMINISTIC:
            continue
        if request.privacy_class not in route.privacy_classes:
            continue
        if request.privacy_class is PrivacyClass.LOCAL_PRIVATE and route.locality is not Locality.LOCAL:
            continue
        health = _health_for(route, request)
        if health in {ProviderHealth.OUTAGE, ProviderHealth.COOLDOWN, ProviderHealth.DISABLED}:
            continue
        if not route.within_budget(request.max_cost_class, request.max_latency_class):
            continue
        if route.capability_score(capability) < minimum:
            continue
        if requires_strong and not route.strong_route:
            continue
        candidates.append(route)

    return tuple(candidates)


def rank_routes(
    registry: tuple[ProviderRoute, ...],
    request: TaskRouteRequest,
) -> tuple[ProviderRoute, ...]:
    candidates = eligible_routes(registry, request)
    capability, _, _ = required_profile(request.task_class, request.required_capability)

    def key(route: ProviderRoute) -> tuple[int, int, int, float, int, str, str]:
        health = _health_for(route, request)
        local_rank = 0 if request.prefer_local and route.locality is Locality.LOCAL else 1
        if route.strong_route:
            local_rank = 1
        return (
            route.fallback_rank,
            local_rank,
            provider_health_rank(health),
            -route.capability_score(capability),
            route.within_budget(request.max_cost_class, request.max_latency_class) is False,
            route.provider_alias or "",
            route.model_alias or "",
        )

    return tuple(sorted(candidates, key=key))


def decide_route(
    registry: tuple[ProviderRoute, ...],
    request: TaskRouteRequest,
) -> PolicyDecision:
    ranked = rank_routes(registry, request)
    considered = tuple(route.route_class for route in ranked)
    if request.task_class is TaskClass.DETERMINISTIC:
        route = ranked[0] if ranked else None
        return PolicyDecision(
            status=RouteStatus.NO_LLM_REQUIRED,
            route=route,
            reason_code="DETERMINISTIC_NO_LLM",
            considered_route_classes=considered,
        )
    if ranked:
        return PolicyDecision(
            status=RouteStatus.SELECTED,
            route=ranked[0],
            reason_code="ROUTE_SELECTED",
            considered_route_classes=considered,
        )
    return PolicyDecision(
        status=RouteStatus.NEEDS_OPERATOR,
        route=None,
        reason_code="NO_ELIGIBLE_BOUNDED_ROUTE",
        considered_route_classes=considered,
    )
