from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class ProviderRegistryError(ValueError):
    pass


class RouteClass(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    LOCAL_LOW_COST = "LOCAL_LOW_COST"
    STRONG_CODING = "STRONG_CODING"
    SECONDARY_CODING = "SECONDARY_CODING"
    CLOUD_STRONG_REVIEW = "CLOUD_STRONG_REVIEW"


class TaskClass(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    EASY_CODING = "EASY_CODING"
    HARD_CODING = "HARD_CODING"
    REVIEW = "REVIEW"
    GENERAL = "GENERAL"


class Capability(StrEnum):
    NONE = "none"
    REASONING = "reasoning"
    REPOSITORY_EDIT = "repository_edit"
    TOOL_USE = "tool_use"
    CODE_REVIEW = "code_review"


class PrivacyClass(StrEnum):
    PUBLIC = "PUBLIC"
    SANITIZED = "SANITIZED"
    LOCAL_PRIVATE = "LOCAL_PRIVATE"


class Locality(StrEnum):
    NONE = "NONE"
    LOCAL = "LOCAL"
    CLOUD = "CLOUD"


class ProviderHealth(StrEnum):
    LIVE = "LIVE"
    DEGRADED = "DEGRADED"
    OUTAGE = "OUTAGE"
    COOLDOWN = "COOLDOWN"
    DISABLED = "DISABLED"


class CostClass(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class LatencyClass(StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


_COST_RANK = {CostClass.NONE: 0, CostClass.LOW: 1, CostClass.MEDIUM: 2, CostClass.HIGH: 3}
_LATENCY_RANK = {
    LatencyClass.NONE: 0,
    LatencyClass.LOW: 1,
    LatencyClass.MEDIUM: 2,
    LatencyClass.HIGH: 3,
}
_HEALTH_RANK = {
    ProviderHealth.LIVE: 0,
    ProviderHealth.DEGRADED: 1,
    ProviderHealth.COOLDOWN: 2,
    ProviderHealth.OUTAGE: 3,
    ProviderHealth.DISABLED: 4,
}


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    route_class: RouteClass
    provider_alias: str | None
    model_alias: str | None
    locality: Locality
    capabilities: Mapping[Capability, float]
    privacy_classes: tuple[PrivacyClass, ...]
    cost_class: CostClass
    latency_class: LatencyClass
    fallback_rank: int
    strong_route: bool = False

    def __post_init__(self) -> None:
        if self.route_class is RouteClass.DETERMINISTIC:
            if self.provider_alias is not None or self.model_alias is not None:
                raise ProviderRegistryError("deterministic_route_cannot_have_model")
            if self.locality is not Locality.NONE:
                raise ProviderRegistryError("deterministic_route_locality_must_be_none")
        else:
            if not self.provider_alias or not self.model_alias:
                raise ProviderRegistryError("model_route_requires_aliases")
            if self.locality is Locality.NONE:
                raise ProviderRegistryError("model_route_requires_locality")
        if self.fallback_rank < 0:
            raise ProviderRegistryError("negative_fallback_rank")
        if not self.privacy_classes:
            raise ProviderRegistryError("privacy_classes_required")
        for capability, score in self.capabilities.items():
            if not isinstance(capability, Capability):
                raise ProviderRegistryError("invalid_capability")
            if not 0.0 <= float(score) <= 1.0:
                raise ProviderRegistryError("capability_score_out_of_range")

    def capability_score(self, capability: Capability) -> float:
        if capability is Capability.NONE:
            return 1.0
        return float(self.capabilities.get(capability, 0.0))

    def within_budget(self, max_cost: CostClass, max_latency: LatencyClass) -> bool:
        return _COST_RANK[self.cost_class] <= _COST_RANK[max_cost] and _LATENCY_RANK[
            self.latency_class
        ] <= _LATENCY_RANK[max_latency]


def provider_health_rank(health: ProviderHealth) -> int:
    return _HEALTH_RANK[health]


def default_provider_registry() -> tuple[ProviderRoute, ...]:
    """Public-safe static roster. Names are aliases, not endpoints or live authority."""
    return (
        ProviderRoute(
            route_class=RouteClass.DETERMINISTIC,
            provider_alias=None,
            model_alias=None,
            locality=Locality.NONE,
            capabilities={Capability.NONE: 1.0},
            privacy_classes=(PrivacyClass.PUBLIC, PrivacyClass.SANITIZED, PrivacyClass.LOCAL_PRIVATE),
            cost_class=CostClass.NONE,
            latency_class=LatencyClass.NONE,
            fallback_rank=0,
        ),
        ProviderRoute(
            route_class=RouteClass.LOCAL_LOW_COST,
            provider_alias="local-private",
            model_alias="local-low-cost",
            locality=Locality.LOCAL,
            capabilities={
                Capability.REASONING: 0.62,
                Capability.REPOSITORY_EDIT: 0.56,
                Capability.TOOL_USE: 0.55,
                Capability.CODE_REVIEW: 0.50,
            },
            privacy_classes=(PrivacyClass.PUBLIC, PrivacyClass.SANITIZED, PrivacyClass.LOCAL_PRIVATE),
            cost_class=CostClass.LOW,
            latency_class=LatencyClass.LOW,
            fallback_rank=10,
        ),
        ProviderRoute(
            route_class=RouteClass.STRONG_CODING,
            provider_alias="cloud-primary",
            model_alias="strong-coding",
            locality=Locality.CLOUD,
            capabilities={
                Capability.REASONING: 0.88,
                Capability.REPOSITORY_EDIT: 0.90,
                Capability.TOOL_USE: 0.88,
                Capability.CODE_REVIEW: 0.82,
            },
            privacy_classes=(PrivacyClass.PUBLIC, PrivacyClass.SANITIZED),
            cost_class=CostClass.HIGH,
            latency_class=LatencyClass.MEDIUM,
            fallback_rank=20,
            strong_route=True,
        ),
        ProviderRoute(
            route_class=RouteClass.SECONDARY_CODING,
            provider_alias="cloud-secondary",
            model_alias="secondary-coding",
            locality=Locality.CLOUD,
            capabilities={
                Capability.REASONING: 0.82,
                Capability.REPOSITORY_EDIT: 0.84,
                Capability.TOOL_USE: 0.82,
                Capability.CODE_REVIEW: 0.76,
            },
            privacy_classes=(PrivacyClass.PUBLIC, PrivacyClass.SANITIZED),
            cost_class=CostClass.MEDIUM,
            latency_class=LatencyClass.MEDIUM,
            fallback_rank=30,
            strong_route=True,
        ),
        ProviderRoute(
            route_class=RouteClass.CLOUD_STRONG_REVIEW,
            provider_alias="cloud-review",
            model_alias="strong-review",
            locality=Locality.CLOUD,
            capabilities={
                Capability.REASONING: 0.86,
                Capability.REPOSITORY_EDIT: 0.72,
                Capability.TOOL_USE: 0.76,
                Capability.CODE_REVIEW: 0.92,
            },
            privacy_classes=(PrivacyClass.PUBLIC, PrivacyClass.SANITIZED),
            cost_class=CostClass.MEDIUM,
            latency_class=LatencyClass.HIGH,
            fallback_rank=40,
            strong_route=True,
        ),
    )
