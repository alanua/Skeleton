from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.capability_model import ObservedDiffImpact
from core.task_quality_gate import TaskSpec


@dataclass(frozen=True, slots=True)
class InvariantEvaluation:
    passed: bool
    failures: tuple[str, ...]


def evaluate_architecture_invariants(
    task: TaskSpec,
    observed: ObservedDiffImpact,
    *,
    evidence_labels: Iterable[str] = (),
) -> InvariantEvaluation:
    failures: set[str] = set()
    capabilities = set(task.requested_capabilities)
    labels = {str(label) for label in evidence_labels}
    if "repository_read" in capabilities and not (
        {"repository_write", "repository_write_allowlisted"} & capabilities
    ):
        if observed.mutating_files or observed.protected_files:
            failures.add("READ_ONLY_ROUTE_MUTATED_PROTECTED_OR_PRODUCTION")
    if "tests_green" in labels and "production_capability_present" not in labels:
        if any("mock" in path or "fixture" in path for path in observed.changed_files):
            failures.add("MOCK_ONLY_PROOF_MISSING_PRODUCTION_CAPABILITY")
    if any("RUNTIME_PROVEN" in label for label in labels):
        failures.add("RUNTIME_PROVEN_NOT_MATERIALIZED_IN_PHASE4")
    if "schema_regression" in labels or "proof_regression" in labels:
        failures.add("CLAIM_PROOF_SCHEMA_REGRESSION")
    return InvariantEvaluation(passed=not failures, failures=tuple(sorted(failures)))
