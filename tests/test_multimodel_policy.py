from __future__ import annotations

from adapters.openrouter_multimodel import build_openrouter_policy_envelope
from core.multimodel_policy import (
    BudgetCeiling,
    MultimodelDecision,
    MultimodelPolicyRequest,
    RequestedMultimodelMode,
    RiskValueEvidence,
    multimodel_policy_request_from_mapping,
    plan_multimodel_policy,
    plan_multimodel_policy_from_mapping,
)


def evidence(
    *,
    ambiguity: float = 0.1,
    impact: float = 0.2,
    value: float = 0.2,
    public_safe: bool = True,
    irreversible: bool = False,
) -> RiskValueEvidence:
    return RiskValueEvidence(
        ambiguity_score=ambiguity,
        failure_impact_score=impact,
        expected_value_score=value,
        public_safe=public_safe,
        irreversible_side_effects=irreversible,
        evidence_refs=("issue:3556",),
    )


_DEFAULT_EVIDENCE = object()


def request(
    *,
    mode: RequestedMultimodelMode = RequestedMultimodelMode.AUTO,
    budget: int = 10,
    risk_value: RiskValueEvidence | None | object = _DEFAULT_EVIDENCE,
) -> MultimodelPolicyRequest:
    return MultimodelPolicyRequest(
        task_type_tag="repository_review",
        evidence=evidence() if risk_value is _DEFAULT_EVIDENCE else risk_value,
        budget_ceiling=BudgetCeiling(max_budget_units=budget),
        requested_mode=mode,
    )


def test_low_risk_auto_routes_to_single_model_with_minimal_reservation() -> None:
    decision = plan_multimodel_policy(request())
    assert decision.decision is MultimodelDecision.SINGLE_MODEL
    assert decision.decision_id == "single_model"
    assert decision.budget_reserved_units == 1


def test_ambiguity_council_is_cheap_diverse_draft_not_adversarial_review() -> None:
    decision = plan_multimodel_policy(request(risk_value=evidence(ambiguity=0.8, impact=0.3, value=0.4)))
    assert decision.decision is MultimodelDecision.AMBIGUITY_COUNCIL
    assert decision.budget_reserved_units == 3
    assert "cheap_diverse_draft_resolution" in decision.reasons
    assert "expensive_adversarial_critique_review" not in decision.reasons


def test_high_impact_high_value_auto_routes_to_adversarial_review_council() -> None:
    decision = plan_multimodel_policy(request(risk_value=evidence(ambiguity=0.2, impact=0.9, value=0.8)))
    assert decision.decision is MultimodelDecision.ADVERSARIAL_REVIEW_COUNCIL
    assert decision.budget_reserved_units == 7
    assert "expensive_adversarial_critique_review" in decision.reasons


def test_missing_risk_value_evidence_fails_closed_to_operator() -> None:
    decision = plan_multimodel_policy(request(risk_value=None))
    assert decision.decision is MultimodelDecision.NEEDS_OPERATOR
    assert decision.budget_reserved_units == 0
    assert decision.reasons == ("risk_value_evidence_required",)


def test_mapping_parser_treats_missing_required_evidence_as_operator_case() -> None:
    parsed = multimodel_policy_request_from_mapping(
        {
            "task_type_tag": "repository_review",
            "requested_mode": "auto",
            "budget_ceiling": {"max_budget_units": 10},
            "risk_value_evidence": {
                "ambiguity_score": 0.9,
                "failure_impact_score": 0.2,
                "public_safe": True,
                "irreversible_side_effects": False,
                "evidence_refs": ["issue:3556"],
            },
        }
    )
    decision = plan_multimodel_policy(parsed)
    assert decision.decision is MultimodelDecision.NEEDS_OPERATOR
    assert decision.reasons == ("risk_value_evidence_required",)


def test_missing_task_type_tag_fails_closed() -> None:
    decision = plan_multimodel_policy(
        MultimodelPolicyRequest(
            task_type_tag=" ",
            evidence=evidence(),
            budget_ceiling=BudgetCeiling(max_budget_units=10),
        )
    )
    assert decision.decision is MultimodelDecision.NEEDS_OPERATOR
    assert decision.reasons == ("task_type_tag_required",)


def test_malformed_boolean_evidence_is_missing_evidence() -> None:
    parsed = multimodel_policy_request_from_mapping(
        {
            "task_type_tag": "repository_review",
            "requested_mode": "auto",
            "budget_ceiling": {"max_budget_units": 10},
            "risk_value_evidence": {
                "ambiguity_score": 0.9,
                "failure_impact_score": 0.2,
                "expected_value_score": 0.4,
                "public_safe": "false",
                "irreversible_side_effects": False,
                "evidence_refs": ["issue:3556"],
            },
        }
    )
    decision = plan_multimodel_policy(parsed)
    assert decision.decision is MultimodelDecision.NEEDS_OPERATOR
    assert decision.reasons == ("risk_value_evidence_required",)


def test_invalid_mapping_request_still_outputs_needs_operator() -> None:
    decision = plan_multimodel_policy_from_mapping(
        {
            "task_type_tag": "repository_review",
            "requested_mode": "surprise_me",
            "budget_ceiling": {"max_budget_units": 10},
            "risk_value_evidence": {
                "ambiguity_score": 0.1,
                "failure_impact_score": 0.1,
                "expected_value_score": 0.1,
                "public_safe": True,
                "irreversible_side_effects": False,
                "evidence_refs": ["issue:3556"],
            },
        }
    )
    assert decision.decision is MultimodelDecision.NEEDS_OPERATOR
    assert decision.reasons == ("policy_request_invalid",)


def test_policy_enforces_budget_reservation_before_selecting_council() -> None:
    decision = plan_multimodel_policy(
        request(
            budget=2,
            mode=RequestedMultimodelMode.AMBIGUITY_COUNCIL,
            risk_value=evidence(ambiguity=0.8),
        )
    )
    assert decision.decision is MultimodelDecision.NEEDS_OPERATOR
    assert decision.reasons == ("budget_reservation_unavailable",)


def test_single_model_request_does_not_silently_downgrade_high_value_review() -> None:
    decision = plan_multimodel_policy(
        request(
            mode=RequestedMultimodelMode.SINGLE_MODEL,
            risk_value=evidence(impact=0.9, value=0.8),
        )
    )
    assert decision.decision is MultimodelDecision.NEEDS_OPERATOR
    assert decision.reasons == ("single_model_requested_for_high_impact_high_value_task",)


def test_private_or_irreversible_evidence_requires_operator() -> None:
    private_decision = plan_multimodel_policy(request(risk_value=evidence(public_safe=False)))
    irreversible_decision = plan_multimodel_policy(request(risk_value=evidence(irreversible=True)))
    assert private_decision.decision is MultimodelDecision.NEEDS_OPERATOR
    assert private_decision.reasons == ("public_safe_evidence_required_for_external_multimodel",)
    assert irreversible_decision.decision is MultimodelDecision.NEEDS_OPERATOR
    assert irreversible_decision.reasons == ("irreversible_side_effects_require_operator",)


def test_openrouter_adapter_does_not_choose_provider_execution_mechanics() -> None:
    decision = plan_multimodel_policy(request(risk_value=evidence(ambiguity=0.9)))
    envelope = build_openrouter_policy_envelope(decision)
    public = envelope.as_public_dict()
    assert public["policy_owner"] == "skeleton"
    assert public["execution_owner"] == "openrouter"
    assert public["decision"] == "ambiguity_council"
    assert public["model_ids"] == ()
    assert "runtime_model" not in public
    assert "credentials" not in public
