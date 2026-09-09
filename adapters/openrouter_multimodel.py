from __future__ import annotations

from dataclasses import asdict, dataclass

from core.multimodel_policy import MultimodelDecision, MultimodelPolicyDecision


@dataclass(frozen=True, slots=True)
class OpenRouterPolicyEnvelope:
    decision: str
    budget_reserved_units: int
    task_type_tag: str
    policy_owner: str
    execution_owner: str
    provider_boundary: str
    provider: str = "openrouter"
    model_ids: tuple[str, ...] = ()

    def as_public_dict(self) -> dict[str, object]:
        return asdict(self)


def build_openrouter_policy_envelope(decision: MultimodelPolicyDecision) -> OpenRouterPolicyEnvelope:
    """Adapt Skeleton's route policy into an OpenRouter-owned execution envelope.

    This helper intentionally carries no model IDs, runtime model names, prompts,
    credentials, SDK settings, or retry mechanics. Those remain provider-owned.
    """
    boundary = (
        "operator_required_before_provider_execution"
        if decision.decision is MultimodelDecision.NEEDS_OPERATOR
        else "provider_selects_execution_mechanics_after_policy"
    )
    return OpenRouterPolicyEnvelope(
        decision=decision.decision.value,
        budget_reserved_units=decision.budget_reserved_units,
        task_type_tag=decision.task_type_tag,
        policy_owner=decision.policy_owner,
        execution_owner="openrouter",
        provider_boundary=boundary,
    )
