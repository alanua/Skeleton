from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from core.task_quality_gate import Phase1TaskClaim, validate_task_claim


PROTECTED_SURFACE: Final = (
    "BOOT_MANIFEST.yaml",
    "PROJECT_TREE.yaml",
    "OPERATOR_RULES.yaml",
    "CAPABILITY_REGISTRY.yaml",
    "INVARIANTS.yaml",
    "scripts/runner_poll_github_tasks.py",
    "core/gate_engine.py",
    "core/action_gate.py",
    "core/architecture_invariants.py",
    ".github/workflows/**",
    "deploy/**",
    "server/**",
    "finance/**",
    "legal/**",
    "governance/**",
    "secrets/**",
    "Runner_core/**",
    "adapter_boundaries/**",
)

PRIVATE_PRIVACY_MARKERS: Final = (
    "PRIVATE",
    "LOCAL_ONLY",
    "LOCAL_PRIVATE",
    "PRIVATE_LOCAL",
    "SECRET",
    "PRIVILEGE",
    "PROTECTED",
)

PUBLIC_SAFE_MARKERS: Final = (
    "PUBLIC_SAFE",
    "PUBLIC-SAFE",
    "PUBLIC_SAFE_POLICY_METADATA_ONLY",
    "PUBLIC_SAFE_HASH_STATUS_ONLY",
)

RISK_ORDER: Final = {"green": 0, "yellow": 1, "red": 2, "critical": 3}


@dataclass(frozen=True)
class QualityEvidence:
    allowed_files: tuple[str, ...]
    protected_files: tuple[str, ...]
    privacy_classification: str
    risk: str
    review_required: bool
    protected_required: bool
    architecture_required: bool
    caller_proof_rejected: tuple[str, ...]

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "allowed_files": list(self.allowed_files),
            "protected_files": list(self.protected_files),
            "privacy_classification": self.public_privacy_classification,
            "risk": self.risk,
            "review_required": self.review_required,
            "protected_required": self.protected_required,
            "architecture_required": self.architecture_required,
            "caller_proof_rejected": list(self.caller_proof_rejected),
        }

    @property
    def public_privacy_classification(self) -> str:
        if self.privacy_classification == "protected_private_public_safe_composite":
            return "protected_private_public_safe_composite_redacted"
        return self.privacy_classification


def build_quality_evidence(value: Mapping[str, Any] | Phase1TaskClaim) -> QualityEvidence:
    claim = value if isinstance(value, Phase1TaskClaim) else validate_task_claim(value)
    protected_files = tuple(
        path for path in claim.allowed_files if is_protected_surface_path(path)
    )
    privacy_classification = classify_privacy(
        claim.privacy,
        claim.privacy_boundary,
    )
    protected_required = bool(protected_files) or privacy_classification.startswith(
        "protected_private"
    )
    risk = _max_risk(
        claim.risk,
        "critical" if protected_required else "green",
    )
    review_required = RISK_ORDER[risk] >= RISK_ORDER["yellow"] or protected_required
    return QualityEvidence(
        allowed_files=claim.allowed_files,
        protected_files=protected_files,
        privacy_classification=privacy_classification,
        risk=risk,
        review_required=review_required,
        protected_required=protected_required,
        architecture_required=False,
        caller_proof_rejected=(
            "ARCHITECTURE_GREEN",
            "PRODUCTION_CONTRACT_GREEN",
            "ObservedDiffImpact",
            "RUNTIME_PROVEN",
        ),
    )


def is_protected_surface_path(path: str) -> bool:
    for protected in PROTECTED_SURFACE:
        if protected.endswith("/**"):
            prefix = protected[:-3]
            if path == protected or path == prefix or path.startswith(prefix + "/"):
                return True
        elif path == protected:
            return True
    return False


def protected_surface_matrix(paths: Iterable[str] = PROTECTED_SURFACE) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "surface": path,
            "protected": True,
            "kind": "directory_scope" if path.endswith("/**") else "exact_path",
        }
        for path in paths
    )


def classify_privacy(privacy: str | None, privacy_boundary: str | None = None) -> str:
    parts = tuple(
        part
        for part in (privacy, privacy_boundary)
        if isinstance(part, str) and part
    )
    haystack = " / ".join(parts).upper()
    has_private = any(marker in haystack for marker in PRIVATE_PRIVACY_MARKERS)
    has_public_safe = any(marker in haystack for marker in PUBLIC_SAFE_MARKERS)
    if has_private and has_public_safe:
        return "protected_private_public_safe_composite"
    if has_private:
        return "protected_private"
    if has_public_safe:
        return "public_safe"
    return "public_review_allowed"


def caller_proof_rejection_matrix() -> tuple[dict[str, str], ...]:
    return (
        {
            "caller_field": "ARCHITECTURE_GREEN",
            "status": "rejected",
            "reason": "architecture proof is later-phase and cannot be caller-satisfied",
        },
        {
            "caller_field": "PRODUCTION_CONTRACT_GREEN",
            "status": "rejected",
            "reason": "production proof is not accepted from caller-shaped data",
        },
        {
            "caller_field": "ObservedDiffImpact/touched_files",
            "status": "rejected",
            "reason": "observed diff impact remains unreachable in Phase 1",
        },
        {
            "caller_field": "RUNTIME_PROVEN",
            "status": "rejected",
            "reason": "runtime proof remains unreachable in Phase 1",
        },
    )


def _max_risk(left: str, right: str) -> str:
    return left if RISK_ORDER[left] >= RISK_ORDER[right] else right
