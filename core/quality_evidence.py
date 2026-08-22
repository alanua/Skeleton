from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final


ARCHITECTURE_REVIEW_REQUIRED: Final = "ARCHITECTURE_REVIEW_REQUIRED"
PRODUCTION_CONTRACT_REVIEW_REQUIRED: Final = "PRODUCTION_CONTRACT_REVIEW_REQUIRED"
RUNTIME_REVIEW_UNREACHED: Final = "RUNTIME_REVIEW_UNREACHED"
CALLER_PROOF_REJECTED: Final = "CALLER_PROOF_REJECTED"
OBSERVED_DIFF_UNREACHED: Final = "OBSERVED_DIFF_UNREACHED"
CLAIM_SIDE_ONLY: Final = "CLAIM_SIDE_ONLY"

_FORBIDDEN_PROOF_STATES: Final = frozenset(
    {
        "ARCHITECTURE_GREEN",
        "RUNTIME_PROVEN",
        "PRODUCTION_CONTRACT_GREEN",
        "OBSERVED_DIFF_IMPACT_PROVEN",
    }
)
_PROOF_LIKE_KEYS: Final = frozenset(
    {
        "architecture_receipt",
        "architecture_state",
        "observed_diff_impact",
        "production_contract_receipt",
        "production_contract_state",
        "runtime_proof",
        "runtime_receipt",
        "runtime_state",
        "touched_files",
    }
)


@dataclass(frozen=True)
class Phase1EvidenceDecision:
    architecture_status: str = ARCHITECTURE_REVIEW_REQUIRED
    production_contract_status: str = PRODUCTION_CONTRACT_REVIEW_REQUIRED
    runtime_status: str = RUNTIME_REVIEW_UNREACHED
    observed_diff_status: str = OBSERVED_DIFF_UNREACHED
    caller_proof_status: str = CLAIM_SIDE_ONLY
    rejected_fields: tuple[str, ...] = ()
    rejected_states: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.rejected_fields and not self.rejected_states

    def to_mapping(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "architecture_status": self.architecture_status,
            "production_contract_status": self.production_contract_status,
            "runtime_status": self.runtime_status,
            "observed_diff_status": self.observed_diff_status,
            "caller_proof_status": self.caller_proof_status,
            "rejected_fields": list(self.rejected_fields),
            "rejected_states": list(self.rejected_states),
        }


def evaluate_phase1_evidence(value: Mapping[str, Any] | None) -> Phase1EvidenceDecision:
    """Classify claim-side evidence without elevating it to proof authority."""
    if value is None:
        return Phase1EvidenceDecision()
    if not isinstance(value, Mapping):
        return Phase1EvidenceDecision(
            caller_proof_status=CALLER_PROOF_REJECTED,
            rejected_fields=("evidence",),
        )

    rejected_fields = tuple(sorted(str(key) for key in value if key in _PROOF_LIKE_KEYS))
    rejected_states = tuple(sorted(_forbidden_states(value)))
    caller_status = (
        CALLER_PROOF_REJECTED
        if rejected_fields or rejected_states
        else CLAIM_SIDE_ONLY
    )
    return Phase1EvidenceDecision(
        caller_proof_status=caller_status,
        rejected_fields=rejected_fields,
        rejected_states=rejected_states,
    )


def _forbidden_states(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in _FORBIDDEN_PROOF_STATES:
            found.add(normalized)
    elif isinstance(value, Mapping):
        for item in value.values():
            found.update(_forbidden_states(item))
    elif isinstance(value, list | tuple):
        for item in value:
            found.update(_forbidden_states(item))
    return found
