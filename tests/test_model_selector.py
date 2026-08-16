from __future__ import annotations

from pathlib import Path

from core.model_registry import CapabilityRecord, ModelRecord, load_model_registry
from core.model_selector import TaskFitRequest, rank_models, select_model


REGISTRY = Path(__file__).resolve().parents[1] / "MODEL_REGISTRY.yaml"


def test_same_profile_same_snapshot_same_ranking() -> None:
    records = load_model_registry(REGISTRY)
    request = TaskFitRequest("coding", {"repository_edit": 0.75, "tool_use": 0.75}, "PUBLIC")
    first = [model.model_id for model in rank_models(records, request)]
    second = [model.model_id for model in rank_models(records, request)]
    assert first == second


def test_tool_use_coding_excludes_response_only_glm_route() -> None:
    records = load_model_registry(REGISTRY)
    request = TaskFitRequest("coding", {"repository_edit": 0.70, "tool_use": 0.70}, "PUBLIC")
    ranked = [model.model_id for model in rank_models(records, request)]
    assert "openrouter-glm-free-challenger" not in ranked
    assert ranked[0] == "openrouter-kimi-k2-challenger"


def test_kimi_can_rank_for_evaluation_but_not_production_before_live_promotion() -> None:
    records = load_model_registry(REGISTRY)
    evaluation = TaskFitRequest("coding", {"repository_edit": 0.70, "tool_use": 0.70}, "PUBLIC")
    production = TaskFitRequest(
        "coding",
        {"repository_edit": 0.70, "tool_use": 0.70},
        "PUBLIC",
        production_only=True,
    )
    assert select_model(records, evaluation).model_id == "openrouter-kimi-k2-challenger"
    assert select_model(records, production) is None


def test_model_can_be_reasoning_eligible_but_repo_edit_ineligible() -> None:
    records = load_model_registry(REGISTRY)
    reasoning = TaskFitRequest("analysis", {"reasoning": 0.70}, "PUBLIC")
    coding = TaskFitRequest("coding", {"repository_edit": 0.70}, "PUBLIC")
    assert "openrouter-glm-free-challenger" in [m.model_id for m in rank_models(records, reasoning)]
    assert "openrouter-glm-free-challenger" not in [m.model_id for m in rank_models(records, coding)]


def test_hard_coding_task_does_not_select_weak_local_for_cost() -> None:
    records = load_model_registry(REGISTRY)
    request = TaskFitRequest("hard-coding", {"repository_edit": 0.80, "tool_use": 0.80}, "PUBLIC")
    selected = select_model(records, request)
    assert selected is not None
    assert selected.model_id == "openrouter-kimi-k2-challenger"


def test_private_task_excludes_cloud_candidates() -> None:
    records = load_model_registry(REGISTRY)
    request = TaskFitRequest("private-analysis", {"reasoning": 0.50}, "PRIVATE_LOCAL")
    ranked = rank_models(records, request)
    assert [model.model_id for model in ranked] == ["local-small"]


def _record(model_id: str, score: float, *, latency: int, cost: int, locality: str = "CLOUD") -> ModelRecord:
    return ModelRecord(
        model_id=model_id,
        provider_family="test",
        locality=locality,
        policy_approved=True,
        health="LIVE",
        privacy_classes=("PUBLIC",),
        latency_rank=latency,
        cost_rank=cost,
        capabilities={"reasoning": CapabilityRecord("reasoning", "LIVE", score, True)},
    )


def test_quality_precedes_latency_and_cost() -> None:
    strong_expensive = _record("strong", 0.90, latency=9, cost=9)
    weak_fast = _record("weak", 0.80, latency=0, cost=0)
    request = TaskFitRequest("analysis", {"reasoning": 0.70}, "PUBLIC")
    assert rank_models((weak_fast, strong_expensive), request)[0].model_id == "strong"


def test_latency_and_cost_break_quality_ties() -> None:
    slow = _record("slow", 0.90, latency=3, cost=0)
    fast = _record("fast", 0.90, latency=1, cost=9)
    request = TaskFitRequest("analysis", {"reasoning": 0.70}, "PUBLIC")
    assert rank_models((slow, fast), request)[0].model_id == "fast"


def test_external_benchmark_only_candidate_is_not_eligible() -> None:
    records = load_model_registry(REGISTRY)
    request = TaskFitRequest("analysis", {"reasoning": 0.50}, "PUBLIC")
    assert "external-benchmark-only" not in [model.model_id for model in rank_models(records, request)]
