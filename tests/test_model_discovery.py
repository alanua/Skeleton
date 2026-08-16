from __future__ import annotations

from core.model_discovery import (
    DiscoveryRequirements,
    DiscoverySignal,
    admit_to_canary,
    shortlist_candidates,
)


def _signal(
    evidence_id: str,
    model_id: str,
    *,
    score: float,
    capabilities: tuple[str, ...] = ("reasoning", "repository_edit", "tool_use"),
    context: int = 128000,
    tools: bool = True,
    cost: int = 2,
    latency: int = 2,
    source: str = "openrouter_ranking",
    available: bool = True,
    privacy: tuple[str, ...] = ("PUBLIC",),
) -> DiscoverySignal:
    return DiscoverySignal(
        evidence_id=evidence_id,
        model_id=model_id,
        provider_family="openrouter",
        source_kind=source,
        available=available,
        external_score=score,
        capabilities=capabilities,
        context_window_tokens=context,
        tool_use_advertised=tools,
        privacy_classes=privacy,
        cost_rank=cost,
        latency_rank=latency,
    )


def test_same_snapshot_same_requirements_same_shortlist() -> None:
    signals = (_signal("a", "model-a", score=0.8), _signal("b", "model-b", score=0.9))
    req = DiscoveryRequirements(("repository_edit", "tool_use"), "PUBLIC", require_tool_use=True)
    first = shortlist_candidates(signals, req)
    second = shortlist_candidates(tuple(reversed(signals)), req)
    assert [item.model_id for item in first] == [item.model_id for item in second]


def test_external_ranking_orders_discovered_only() -> None:
    signals = (_signal("a", "model-a", score=0.75), _signal("b", "model-b", score=0.95))
    req = DiscoveryRequirements(("reasoning",), "PUBLIC")
    candidates = shortlist_candidates(signals, req)
    assert [item.model_id for item in candidates] == ["model-b", "model-a"]
    assert {item.stage for item in candidates} == {"DISCOVERED"}


def test_fit_filters_apply_before_canary_shortlist() -> None:
    signals = (
        _signal("good", "good", score=0.7),
        _signal("no-tools", "no-tools", score=0.99, tools=False),
        _signal("short", "short", score=0.99, context=16000),
        _signal("expensive", "expensive", score=0.99, cost=9),
        _signal("missing-cap", "missing-cap", score=0.99, capabilities=("reasoning",)),
    )
    req = DiscoveryRequirements(
        ("repository_edit", "tool_use"),
        "PUBLIC",
        min_context_tokens=64000,
        require_tool_use=True,
        max_cost_rank=3,
        allowed_provider_families=("openrouter",),
    )
    assert [item.model_id for item in shortlist_candidates(signals, req)] == ["good"]


def test_unavailable_and_privacy_incompatible_candidates_are_filtered() -> None:
    signals = (
        _signal("offline", "offline", score=1.0, available=False),
        _signal("public", "public", score=0.9),
        _signal("private", "private", score=0.8, privacy=("PRIVATE_LOCAL",)),
    )
    req = DiscoveryRequirements(("reasoning",), "PRIVATE_LOCAL")
    assert [item.model_id for item in shortlist_candidates(signals, req)] == ["private"]


def test_canary_admission_requires_policy_and_privacy() -> None:
    candidate = shortlist_candidates(
        (_signal("a", "model-a", score=0.8),),
        DiscoveryRequirements(("reasoning",), "PUBLIC"),
    )[0]
    assert admit_to_canary(candidate, policy_approved=True, privacy_approved=True).stage == "CANARY_ONLY"
    assert admit_to_canary(candidate, policy_approved=False, privacy_approved=True).stage == "BLOCKED"
    assert admit_to_canary(candidate, policy_approved=True, privacy_approved=False).stage == "BLOCKED"


def test_multiple_external_sources_aggregate_without_promotion() -> None:
    signals = (
        _signal(
            "catalog",
            "model-a",
            score=0.4,
            source="openrouter_catalog",
            capabilities=("reasoning", "repository_edit"),
            tools=False,
            context=256000,
            cost=3,
        ),
        _signal(
            "ranking",
            "model-a",
            score=0.92,
            source="openrouter_ranking",
            capabilities=("reasoning", "tool_use"),
            tools=True,
            context=128000,
            cost=2,
        ),
    )
    candidate = shortlist_candidates(
        signals,
        DiscoveryRequirements(("repository_edit", "tool_use"), "PUBLIC", require_tool_use=True),
    )[0]
    assert candidate.stage == "DISCOVERED"
    assert candidate.external_score == 0.92
    assert candidate.context_window_tokens == 256000
    assert candidate.evidence_ids == ("catalog", "ranking")
