from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.model_registry import ModelRecord


class ModelSelectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TaskFitRequest:
    task_class: str
    required_capabilities: Mapping[str, float]
    privacy_class: str
    prefer_local: bool = True
    production_only: bool = False

    def __post_init__(self) -> None:
        if not self.task_class:
            raise ModelSelectionError("task_class_required")
        if not self.privacy_class:
            raise ModelSelectionError("privacy_class_required")
        if not self.required_capabilities:
            raise ModelSelectionError("required_capabilities_required")
        for capability_id, minimum_score in self.required_capabilities.items():
            if not capability_id:
                raise ModelSelectionError("capability_id_required")
            if not 0.0 <= float(minimum_score) <= 1.0:
                raise ModelSelectionError("minimum_score_out_of_range")


def _health_rank(health: str) -> int:
    return {"LIVE": 0, "DEGRADED": 1, "COOLDOWN": 2, "DISABLED": 3}.get(health, 99)


def _eligible(model: ModelRecord, request: TaskFitRequest) -> bool:
    if not model.policy_approved:
        return False
    if model.health in {"COOLDOWN", "DISABLED"}:
        return False
    if request.privacy_class not in model.privacy_classes:
        return False
    for capability_id, minimum_score in request.required_capabilities.items():
        capability = model.capability(capability_id)
        if capability is None:
            return False
        if request.production_only:
            if not capability.production_eligible(float(minimum_score)):
                return False
        elif not capability.eligible(float(minimum_score)):
            return False
    return True


def rank_models(
    models: tuple[ModelRecord, ...],
    request: TaskFitRequest,
) -> tuple[ModelRecord, ...]:
    """Deterministically rank only policy- and evidence-eligible models.

    External discovery scores never enter this function. Required capability quality and
    privacy are hard gates. For production_only requests every required capability must
    also have explicit LIVE promotion. Locality, health, latency and cost are tie-breaks.
    """
    eligible = [model for model in models if _eligible(model, request)]

    def key(model: ModelRecord) -> tuple[float, int, int, int, int, str]:
        quality = sum(
            model.capabilities[capability_id].score
            for capability_id in sorted(request.required_capabilities)
        )
        locality_rank = 0 if request.prefer_local and model.locality == "LOCAL" else 1
        return (
            -quality,
            locality_rank,
            _health_rank(model.health),
            model.latency_rank,
            model.cost_rank,
            model.model_id,
        )

    return tuple(sorted(eligible, key=key))


def select_model(
    models: tuple[ModelRecord, ...],
    request: TaskFitRequest,
) -> ModelRecord | None:
    ranked = rank_models(models, request)
    return ranked[0] if ranked else None
