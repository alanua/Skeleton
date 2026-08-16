from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from core.model_registry import CapabilityRecord


HARD_FAILURE_CLASSES = frozenset(
    {
        "DELIVERABLE_MISSING",
        "TOOL_USE_FAILED",
        "SCOPE_VIOLATION",
        "PRIVACY_VIOLATION",
        "VALIDATION_FAILED",
    }
)


class ModelBenchmarkError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BenchmarkReceipt:
    evidence_id: str
    model_id: str
    capability_id: str
    source_kind: str
    passed: bool
    quality_score: float
    hard_failures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.model_id or not self.capability_id:
            raise ModelBenchmarkError("benchmark_identity_required")
        if self.source_kind not in {"skeleton_canary", "external_benchmark"}:
            raise ModelBenchmarkError("invalid_benchmark_source")
        if not 0.0 <= float(self.quality_score) <= 1.0:
            raise ModelBenchmarkError("benchmark_score_out_of_range")
        if any(item not in HARD_FAILURE_CLASSES for item in self.hard_failures):
            raise ModelBenchmarkError("unknown_hard_failure")
        if self.passed and self.hard_failures:
            raise ModelBenchmarkError("passed_receipt_cannot_have_hard_failure")

    @property
    def is_skeleton_canary_pass(self) -> bool:
        return self.source_kind == "skeleton_canary" and self.passed and not self.hard_failures


def summarize_capability(
    capability_id: str,
    receipts: Iterable[BenchmarkReceipt],
) -> CapabilityRecord:
    relevant = tuple(receipt for receipt in receipts if receipt.capability_id == capability_id)
    if not relevant:
        return CapabilityRecord(capability_id, "UNSUPPORTED", 0.0, False)

    hard_failures = tuple(
        sorted({failure for receipt in relevant for failure in receipt.hard_failures})
    )
    skeleton_passes = tuple(receipt for receipt in relevant if receipt.is_skeleton_canary_pass)
    evidence_ids = tuple(sorted(receipt.evidence_id for receipt in relevant))

    if hard_failures:
        return CapabilityRecord(
            capability_id=capability_id,
            status="UNSUPPORTED",
            score=max((receipt.quality_score for receipt in relevant), default=0.0),
            canary_passed=False,
            hard_failures=hard_failures,
            evidence_ids=evidence_ids,
        )

    if not skeleton_passes:
        return CapabilityRecord(
            capability_id=capability_id,
            status="DEGRADED",
            score=max((receipt.quality_score for receipt in relevant), default=0.0),
            canary_passed=False,
            evidence_ids=evidence_ids,
        )

    return CapabilityRecord(
        capability_id=capability_id,
        status="LIVE",
        score=max(receipt.quality_score for receipt in skeleton_passes),
        canary_passed=True,
        evidence_ids=evidence_ids,
    )
