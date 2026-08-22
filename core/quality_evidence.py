from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any, Final


ARCHITECTURE_REVIEW_REQUIRED: Final = "ARCHITECTURE_REVIEW_REQUIRED"
PRODUCTION_CONTRACT_REVIEW_REQUIRED: Final = "PRODUCTION_CONTRACT_REVIEW_REQUIRED"
PROTECTED_REVIEW_REQUIRED: Final = "PROTECTED_REVIEW_REQUIRED"
PUBLIC_REVIEW_ALLOWED: Final = "PUBLIC_REVIEW_ALLOWED"

INVALID_PHASE1_PROOF_CLAIMS: Final = frozenset(
    {
        "ARCHITECTURE_GREEN",
        "RUNTIME_PROVEN",
    }
)

PRIVATE_PRIVACY_BOUNDARIES: Final = frozenset(
    {
        "LOCAL_PRIVATE",
        "PRIVATE_LOCAL",
        "PRIVATE",
        "PRIVATE_REPOSITORY_AND_POLICY_METADATA",
        "PUBLIC_SAFE_POLICY_METADATA_ONLY+PRIVATE_RUNTIME_CONTEXT",
    }
)
PUBLIC_PRIVACY_BOUNDARIES: Final = frozenset(
    {
        "PUBLIC_SAFE",
        "PUBLIC_SAFE_REPOSITORY_ONLY",
        "PUBLIC_SAFE_AGGREGATE_ONLY",
        "PUBLIC_SAFE_POLICY_METADATA_ONLY",
    }
)
PRIVACY_BOUNDARIES: Final = PUBLIC_PRIVACY_BOUNDARIES | PRIVATE_PRIVACY_BOUNDARIES

PROTECTED_RISK_LEVELS: Final = frozenset({"HIGH", "CRITICAL", "PROTECTED"})
LOW_RISK_LEVELS: Final = frozenset({"LOW", "GREEN", "BENIGN", "PUBLIC_SAFE"})
RISK_LEVELS: Final = LOW_RISK_LEVELS | PROTECTED_RISK_LEVELS | frozenset({"MEDIUM"})


class QualityEvidenceError(ValueError):
    """Raised when Phase 1 quality evidence input is malformed or unsafe."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class Phase1EvidenceClassification:
    architecture_status: str | None
    production_contract_status: str | None
    protected_status: str
    privacy_classification: str
    risk_classification: str
    reasons: tuple[str, ...]

    @property
    def review_requirements(self) -> tuple[str, ...]:
        requirements: list[str] = []
        if self.architecture_status is not None:
            requirements.append(self.architecture_status)
        if self.production_contract_status is not None:
            requirements.append(self.production_contract_status)
        if self.protected_status == PROTECTED_REVIEW_REQUIRED:
            requirements.append(PROTECTED_REVIEW_REQUIRED)
        return tuple(requirements)


def freeze_json(value: object, *, path: str = "value", depth: int = 0) -> Any:
    if depth > 24:
        raise QualityEvidenceError("JSON_TOO_DEEP", f"{path} exceeds maximum depth")
    if value is None or isinstance(value, (bool, int, float, str)):
        _assert_json_scalar(value, path)
        return value
    if isinstance(value, Mapping):
        if len(value) > 512:
            raise QualityEvidenceError("JSON_TOO_LARGE", f"{path} has too many fields")
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str) or not key or _has_control_char(key):
                raise QualityEvidenceError("INVALID_JSON_KEY", f"{path} has invalid keys")
            normalized[key] = freeze_json(
                value[key],
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return MappingProxyType(normalized)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 512:
            raise QualityEvidenceError("JSON_TOO_LARGE", f"{path} has too many items")
        return tuple(
            freeze_json(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        )
    raise QualityEvidenceError("INVALID_JSON", f"{path} contains non-JSON data")


def thaw_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(child) for child in value]
    return value


def canonical_json(value: object) -> str:
    return json.dumps(
        thaw_json(freeze_json(value)),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def assert_no_phase1_proof_claims(value: object, *, path: str = "input") -> None:
    if isinstance(value, str):
        token = value.strip().upper()
        if token in INVALID_PHASE1_PROOF_CLAIMS:
            raise QualityEvidenceError(
                "INVALID_PHASE1_PROOF_CLAIM",
                f"{path} contains caller-supplied {token}",
            )
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str):
                token = key.strip().upper()
                if token in INVALID_PHASE1_PROOF_CLAIMS:
                    raise QualityEvidenceError(
                        "INVALID_PHASE1_PROOF_CLAIM",
                        f"{path}.{key} contains caller-supplied {token}",
                    )
                child_path = f"{path}.{key}"
            else:
                child_path = path
            assert_no_phase1_proof_claims(child, path=child_path)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            assert_no_phase1_proof_claims(child, path=f"{path}[{index}]")


def classify_phase1_evidence(
    *,
    architecture_required: bool,
    production_contract_required: bool,
    protected_scope_declared: bool,
    protected_intent: bool,
    privacy_boundary: str,
    risk: str,
) -> Phase1EvidenceClassification:
    normalized_privacy = privacy_boundary.strip().upper()
    normalized_risk = risk.strip().upper()
    if normalized_privacy not in PRIVACY_BOUNDARIES:
        raise QualityEvidenceError(
            "INVALID_PRIVACY_BOUNDARY",
            "privacy_boundary is not allowlisted",
        )
    if normalized_risk not in RISK_LEVELS:
        raise QualityEvidenceError("INVALID_RISK", "risk is not allowlisted")

    privacy_private = normalized_privacy in PRIVATE_PRIVACY_BOUNDARIES
    risk_protected = normalized_risk in PROTECTED_RISK_LEVELS
    protected_review = (
        protected_scope_declared
        or protected_intent
        or privacy_private
        or risk_protected
    )
    reasons: list[str] = []
    if protected_scope_declared:
        reasons.append("protected declared scope")
    if protected_intent:
        reasons.append("explicit protected intent")
    if privacy_private:
        reasons.append("private or composite privacy boundary")
    if risk_protected:
        reasons.append("protected risk level")

    return Phase1EvidenceClassification(
        architecture_status=(
            ARCHITECTURE_REVIEW_REQUIRED if architecture_required else None
        ),
        production_contract_status=(
            PRODUCTION_CONTRACT_REVIEW_REQUIRED
            if production_contract_required
            else None
        ),
        protected_status=(
            PROTECTED_REVIEW_REQUIRED if protected_review else PUBLIC_REVIEW_ALLOWED
        ),
        privacy_classification="PRIVATE_OR_PROTECTED" if privacy_private else "PUBLIC_SAFE",
        risk_classification=(
            "PROTECTED_RISK" if risk_protected else normalized_risk
        ),
        reasons=tuple(reasons),
    )


def public_evidence_mapping(
    classification: Phase1EvidenceClassification,
) -> dict[str, Any]:
    return {
        "architecture_status": classification.architecture_status,
        "production_contract_status": classification.production_contract_status,
        "protected_status": classification.protected_status,
        "privacy_classification": classification.privacy_classification,
        "risk_classification": classification.risk_classification,
        "review_requirements": list(classification.review_requirements),
        "reasons": list(classification.reasons),
    }


def _assert_json_scalar(value: object, path: str) -> None:
    if isinstance(value, str):
        if _has_control_char(value):
            raise QualityEvidenceError("INVALID_JSON_STRING", f"{path} has control characters")
        if len(value) > 8192:
            raise QualityEvidenceError("JSON_STRING_TOO_LONG", f"{path} is too long")
    if isinstance(value, float):
        json.dumps(value, allow_nan=False)


def _has_control_char(value: str) -> bool:
    return any(ord(character) < 32 for character in value)
