from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable


DISCOVERY_SOURCE_KINDS = frozenset(
    {"openrouter_catalog", "openrouter_ranking", "official_metadata", "external_benchmark"}
)
DISCOVERY_STAGES = frozenset({"DISCOVERED", "CANARY_ONLY", "BLOCKED"})


class ModelDiscoveryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DiscoverySignal:
    evidence_id: str
    model_id: str
    provider_family: str
    source_kind: str
    available: bool
    external_score: float
    capabilities: tuple[str, ...]
    context_window_tokens: int
    tool_use_advertised: bool
    privacy_classes: tuple[str, ...]
    cost_rank: int
    latency_rank: int

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.model_id or not self.provider_family:
            raise ModelDiscoveryError("discovery_identity_required")
        if self.source_kind not in DISCOVERY_SOURCE_KINDS:
            raise ModelDiscoveryError("invalid_discovery_source")
        if not 0.0 <= float(self.external_score) <= 1.0:
            raise ModelDiscoveryError("external_score_out_of_range")
        if self.context_window_tokens < 0 or self.cost_rank < 0 or self.latency_rank < 0:
            raise ModelDiscoveryError("negative_discovery_metric")
        if any(not item for item in self.capabilities):
            raise ModelDiscoveryError("invalid_advertised_capability")
        if any(not item for item in self.privacy_classes):
            raise ModelDiscoveryError("invalid_privacy_class")


@dataclass(frozen=True, slots=True)
class DiscoveryRequirements:
    required_capabilities: tuple[str, ...]
    privacy_class: str
    min_context_tokens: int = 0
    require_tool_use: bool = False
    max_cost_rank: int | None = None
    allowed_provider_families: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.required_capabilities or any(not item for item in self.required_capabilities):
            raise ModelDiscoveryError("required_capabilities_required")
        if not self.privacy_class:
            raise ModelDiscoveryError("privacy_class_required")
        if self.min_context_tokens < 0:
            raise ModelDiscoveryError("negative_context_requirement")
        if self.max_cost_rank is not None and self.max_cost_rank < 0:
            raise ModelDiscoveryError("negative_cost_limit")


@dataclass(frozen=True, slots=True)
class DiscoveredCandidate:
    model_id: str
    provider_family: str
    stage: str
    external_score: float
    capabilities: tuple[str, ...]
    context_window_tokens: int
    tool_use_advertised: bool
    privacy_classes: tuple[str, ...]
    cost_rank: int
    latency_rank: int
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.stage not in DISCOVERY_STAGES:
            raise ModelDiscoveryError("invalid_discovery_stage")


def _aggregate_model(signals: tuple[DiscoverySignal, ...]) -> DiscoveredCandidate:
    provider_families = {signal.provider_family for signal in signals}
    if len(provider_families) != 1:
        raise ModelDiscoveryError("conflicting_provider_family")
    return DiscoveredCandidate(
        model_id=signals[0].model_id,
        provider_family=signals[0].provider_family,
        stage="DISCOVERED",
        external_score=max(signal.external_score for signal in signals),
        capabilities=tuple(sorted({item for signal in signals for item in signal.capabilities})),
        context_window_tokens=max(signal.context_window_tokens for signal in signals),
        tool_use_advertised=any(signal.tool_use_advertised for signal in signals),
        privacy_classes=tuple(sorted({item for signal in signals for item in signal.privacy_classes})),
        cost_rank=min(signal.cost_rank for signal in signals),
        latency_rank=min(signal.latency_rank for signal in signals),
        evidence_ids=tuple(sorted(signal.evidence_id for signal in signals)),
    )


def shortlist_candidates(
    signals: Iterable[DiscoverySignal],
    requirements: DiscoveryRequirements,
) -> tuple[DiscoveredCandidate, ...]:
    """Return deterministic DISCOVERED candidates for bounded canary admission.

    External rank/score influences shortlist order only. This function cannot create
    ELIGIBLE or LIVE model state and performs no provider calls.
    """
    grouped: dict[str, list[DiscoverySignal]] = {}
    for signal in signals:
        if signal.available:
            grouped.setdefault(signal.model_id, []).append(signal)

    candidates: list[DiscoveredCandidate] = []
    required = set(requirements.required_capabilities)
    allowed_providers = set(requirements.allowed_provider_families)
    for model_id in sorted(grouped):
        candidate = _aggregate_model(tuple(grouped[model_id]))
        if allowed_providers and candidate.provider_family not in allowed_providers:
            continue
        if not required.issubset(candidate.capabilities):
            continue
        if requirements.privacy_class not in candidate.privacy_classes:
            continue
        if candidate.context_window_tokens < requirements.min_context_tokens:
            continue
        if requirements.require_tool_use and not candidate.tool_use_advertised:
            continue
        if requirements.max_cost_rank is not None and candidate.cost_rank > requirements.max_cost_rank:
            continue
        candidates.append(candidate)

    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -candidate.external_score,
                candidate.cost_rank,
                candidate.latency_rank,
                candidate.model_id,
            ),
        )
    )


def admit_to_canary(
    candidate: DiscoveredCandidate,
    *,
    policy_approved: bool,
    privacy_approved: bool,
) -> DiscoveredCandidate:
    """Move DISCOVERED to CANARY_ONLY only after explicit policy/privacy admission."""
    if candidate.stage != "DISCOVERED":
        raise ModelDiscoveryError("candidate_not_discovered")
    if not policy_approved or not privacy_approved:
        return replace(candidate, stage="BLOCKED")
    return replace(candidate, stage="CANARY_ONLY")
