from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .patch_validator import PatchValidator


class GateResult(Enum):
    ALLOWED = "allowed"
    BLOCKED_NO_PLAN = "blocked_no_plan"
    BLOCKED_NO_APPROVAL = "blocked_no_approval"
    BLOCKED_READ_MISSING = "blocked_read_missing"
    BLOCKED_INVALID_SCHEMA = "blocked_invalid_schema"


@dataclass(frozen=True)
class GateDecision:
    result: GateResult
    reasons: tuple[str, ...]


class GateEngine:
    def __init__(self, validator: Optional[PatchValidator] = None) -> None:
        self._validator = validator or PatchValidator()

    def check_patch_plan(self, plan: Optional[dict]) -> GateDecision:
        if plan is None:
            return GateDecision(
                result=GateResult.BLOCKED_NO_PLAN,
                reasons=("PatchPlan is required before a durable write.",),
            )

        errors = tuple(self._validator.validate(plan))
        if errors:
            return GateDecision(
                result=GateResult.BLOCKED_INVALID_SCHEMA,
                reasons=errors,
            )

        if plan.get("current_rule_read") is not True:
            return GateDecision(
                result=GateResult.BLOCKED_READ_MISSING,
                reasons=("current_rule_read must be true before a durable write.",),
            )

        if plan.get("approval_required") is True and plan.get("operator_approval") is not True:
            return GateDecision(
                result=GateResult.BLOCKED_NO_APPROVAL,
                reasons=("operator_approval must be true when approval_required is true.",),
            )

        return GateDecision(
            result=GateResult.ALLOWED,
            reasons=(),
        )
