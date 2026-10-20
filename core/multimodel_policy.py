from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class MultimodelPolicyError(ValueError):
    pass


class RequestedMultimodelMode(StrEnum):
    AUTO = "auto"
    SINGLE_MODEL = "single_model"
    AMBIGUITY_COUNCIL = "ambiguity_council"
    ADVERSARIAL_REVIEW_COUNCIL = "adversarial_review_council"


class MultimodelDecision(StrEnum):
    SINGLE_MODEL = "single_model"
    AMBIGUITY_COUNCIL = "ambiguity_council"
    ADVERSARIAL_REVIEW_COUNCIL = "adversarial_review_council"
    NEEDS_OPERATOR = "needs_operator"


_REQUIRED_EVIDENCE_FIELDS = frozenset(
    {
        "ambiguity_score",
        "failure_impact_score",
        "expected_value_score",
        "public_safe",
        "irreversible_side_effects",
        "evidence_refs",
    }
)

_RESERVATION_BY_DECISION = {
    MultimodelDecision.SINGLE_MODEL: 1,
    MultimodelDecision.AMBIGUITY_COUNCIL: 3,
    MultimodelDecision.ADVERSARIAL_REVIEW_COUNCIL: 7,
}

_AMBIGUITY_THRESHOLD = 0.55
_ADVERSARIAL_IMPACT_THRESHOLD = 0.75
_ADVERSARIAL_VALUE_THRESHOLD = 0.55


@dataclass(frozen=True, slots=True)
class RiskValueEvidence:
    ambiguity_score: float
    failure_impact_score: float
    expected_value_score: float
    public_safe: bool
    irreversible_side_effects: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in ("ambiguity_score", "failure_impact_score", "expected_value_score"):
            value = float(getattr(self, field_name))
            if not 0.0 <= value <= 1.0:
                raise MultimodelPolicyError(f"{field_name}_out_of_range")
            object.__setattr__(self, field_name, value)
        if not self.evidence_refs:
            raise MultimodelPolicyError("evidence_refs_required")
        for ref in self.evidence_refs:
            if not ref:
                raise MultimodelPolicyError("evidence_ref_required")


@dataclass(frozen=True, slots=True)
class BudgetCeiling:
    max_budget_units: int
    already_reserved_units: int = 0

    def __post_init__(self) -> None:
        if self.max_budget_units < 0 or self.already_reserved_units < 0:
            raise MultimodelPolicyError("budget_ceiling_invalid")
        if self.already_reserved_units > self.max_budget_units:
            raise MultimodelPolicyError("budget_ceiling_exceeded")

    @property
    def available_units(self) -> int:
        return self.max_budget_units - self.already_reserved_units


@dataclass(frozen=True, slots=True)
class MultimodelPolicyRequest:
    task_type_tag: str
    evidence: RiskValueEvidence | None
    budget_ceiling: BudgetCeiling
    requested_mode: RequestedMultimodelMode = RequestedMultimodelMode.AUTO

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_type_tag", self.task_type_tag.strip())


@dataclass(frozen=True, slots=True)
class MultimodelPolicyDecision:
    decision: MultimodelDecision
    budget_reserved_units: int
    requested_mode: RequestedMultimodelMode
    task_type_tag: str
    reasons: tuple[str, ...]
    policy_owner: str = "skeleton"
    execution_owner: str = "provider_adapter"

    @property
    def decision_id(self) -> str:
        return self.decision.value


def evidence_from_mapping(payload: Mapping[str, object]) -> RiskValueEvidence | None:
    missing = _REQUIRED_EVIDENCE_FIELDS - set(payload)
    if missing:
        return None
    refs = payload.get("evidence_refs")
    if not isinstance(refs, (list, tuple)):
        return None
    public_safe = payload.get("public_safe")
    irreversible_side_effects = payload.get("irreversible_side_effects")
    if not isinstance(public_safe, bool) or not isinstance(irreversible_side_effects, bool):
        return None
    try:
        return RiskValueEvidence(
            ambiguity_score=float(payload["ambiguity_score"]),
            failure_impact_score=float(payload["failure_impact_score"]),
            expected_value_score=float(payload["expected_value_score"]),
            public_safe=public_safe,
            irreversible_side_effects=irreversible_side_effects,
            evidence_refs=tuple(str(ref) for ref in refs),
        )
    except (TypeError, ValueError, MultimodelPolicyError):
        return None


def multimodel_policy_request_from_mapping(payload: Mapping[str, object]) -> MultimodelPolicyRequest:
    raw_budget = payload.get("budget_ceiling", {})
    if not isinstance(raw_budget, Mapping):
        raise MultimodelPolicyError("budget_ceiling_mapping_required")
    raw_evidence = payload.get("risk_value_evidence")
    evidence = evidence_from_mapping(raw_evidence) if isinstance(raw_evidence, Mapping) else None
    return MultimodelPolicyRequest(
        task_type_tag=str(payload.get("task_type_tag", "")),
        evidence=evidence,
        budget_ceiling=BudgetCeiling(
            max_budget_units=int(raw_budget.get("max_budget_units", -1)),
            already_reserved_units=int(raw_budget.get("already_reserved_units", 0)),
        ),
        requested_mode=RequestedMultimodelMode(str(payload.get("requested_mode", RequestedMultimodelMode.AUTO.value))),
    )


def plan_multimodel_policy_from_mapping(payload: Mapping[str, object]) -> MultimodelPolicyDecision:
    try:
        request = multimodel_policy_request_from_mapping(payload)
    except (TypeError, ValueError, MultimodelPolicyError):
        return MultimodelPolicyDecision(
            decision=MultimodelDecision.NEEDS_OPERATOR,
            budget_reserved_units=0,
            requested_mode=RequestedMultimodelMode.AUTO,
            task_type_tag=str(payload.get("task_type_tag", "")).strip(),
            reasons=("policy_request_invalid",),
        )
    return plan_multimodel_policy(request)


def plan_multimodel_policy(request: MultimodelPolicyRequest) -> MultimodelPolicyDecision:
    if not request.task_type_tag:
        return _needs_operator(request, "task_type_tag_required")
    evidence = request.evidence
    if evidence is None:
        return _needs_operator(request, "risk_value_evidence_required")
    if not evidence.public_safe:
        return _needs_operator(request, "public_safe_evidence_required_for_external_multimodel")
    if evidence.irreversible_side_effects:
        return _needs_operator(request, "irreversible_side_effects_require_operator")

    target = _target_decision(request.requested_mode, evidence)
    if target is MultimodelDecision.NEEDS_OPERATOR:
        return _needs_operator(request, "requested_mode_requires_operator")
    if request.requested_mode is RequestedMultimodelMode.SINGLE_MODEL and _requires_adversarial_review(evidence):
        return _needs_operator(request, "single_model_requested_for_high_impact_high_value_task")

    reserved = _RESERVATION_BY_DECISION[target]
    if reserved > request.budget_ceiling.available_units:
        return _needs_operator(request, "budget_reservation_unavailable")

    reasons = [_reason_for(target)]
    if target is MultimodelDecision.AMBIGUITY_COUNCIL:
        reasons.append("cheap_diverse_draft_resolution")
    if target is MultimodelDecision.ADVERSARIAL_REVIEW_COUNCIL:
        reasons.append("expensive_adversarial_critique_review")
    return MultimodelPolicyDecision(
        decision=target,
        budget_reserved_units=reserved,
        requested_mode=request.requested_mode,
        task_type_tag=request.task_type_tag,
        reasons=tuple(reasons),
    )


def _target_decision(requested: RequestedMultimodelMode, evidence: RiskValueEvidence) -> MultimodelDecision:
    if requested is RequestedMultimodelMode.SINGLE_MODEL:
        return MultimodelDecision.SINGLE_MODEL
    if requested is RequestedMultimodelMode.AMBIGUITY_COUNCIL:
        return MultimodelDecision.AMBIGUITY_COUNCIL
    if requested is RequestedMultimodelMode.ADVERSARIAL_REVIEW_COUNCIL:
        return MultimodelDecision.ADVERSARIAL_REVIEW_COUNCIL
    if _requires_adversarial_review(evidence):
        return MultimodelDecision.ADVERSARIAL_REVIEW_COUNCIL
    if evidence.ambiguity_score >= _AMBIGUITY_THRESHOLD:
        return MultimodelDecision.AMBIGUITY_COUNCIL
    return MultimodelDecision.SINGLE_MODEL


def _requires_adversarial_review(evidence: RiskValueEvidence) -> bool:
    return (
        evidence.failure_impact_score >= _ADVERSARIAL_IMPACT_THRESHOLD
        and evidence.expected_value_score >= _ADVERSARIAL_VALUE_THRESHOLD
    )


def _needs_operator(request: MultimodelPolicyRequest, reason: str) -> MultimodelPolicyDecision:
    return MultimodelPolicyDecision(
        decision=MultimodelDecision.NEEDS_OPERATOR,
        budget_reserved_units=0,
        requested_mode=request.requested_mode,
        task_type_tag=request.task_type_tag,
        reasons=(reason,),
    )


def _reason_for(decision: MultimodelDecision) -> str:
    return {
        MultimodelDecision.SINGLE_MODEL: "bounded_single_model_sufficient",
        MultimodelDecision.AMBIGUITY_COUNCIL: "ambiguity_resolution_worth_budget",
        MultimodelDecision.ADVERSARIAL_REVIEW_COUNCIL: "risk_value_review_worth_budget",
    }[decision]
