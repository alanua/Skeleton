from __future__ import annotations

from dataclasses import dataclass

from core.capability_model import ImpactLevel, ObservedDiffImpact, PredictedImpact, max_impact


@dataclass(frozen=True, slots=True)
class ClaimReconciliation:
    accepted: bool
    level: ImpactLevel
    reasons: tuple[str, ...]
    deterministic_failure: bool = False


def reconcile_claims(
    predicted: PredictedImpact,
    observed: ObservedDiffImpact,
    *,
    declared_tests_green: bool,
    declared_impact: ImpactLevel | str | None = None,
) -> ClaimReconciliation:
    declared = _impact(declared_impact) if declared_impact is not None else predicted.level
    level = max_impact(predicted.level, observed.level, declared)
    reasons = set(predicted.reasons) | set(observed.reasons)
    if declared_tests_green:
        reasons.add("tests_declared_green")
    if declared is ImpactLevel.GREEN and observed.level in {ImpactLevel.RED, ImpactLevel.PROTECTED}:
        reasons.add("declared_green_conflicts_with_actual_diff")
        return ClaimReconciliation(False, observed.level, tuple(sorted(reasons)), True)
    return ClaimReconciliation(True, level, tuple(sorted(reasons)), False)


def _impact(value: ImpactLevel | str) -> ImpactLevel:
    if isinstance(value, ImpactLevel):
        return value
    return ImpactLevel(str(value).lower())
