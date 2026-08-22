from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from core.quality_evidence import ReviewEvidence, is_full_sha, stable_public_hash


ARCHITECTURE_INVARIANTS_SCHEMA: Final = "skeleton.architecture_invariants.v1"

PROTECTED_PATTERNS: Final = (
    "BOOT_MANIFEST.yaml",
    "PROJECT_TREE.yaml",
    "OPERATOR_RULES.yaml",
    "CAPABILITY_REGISTRY.yaml",
    ".github/workflows",
    "scripts/runner_poll_github_tasks.py",
    "core/gate_engine.py",
    "core/action_gate.py",
    "secrets",
    "deploy",
    "server",
    "finance",
    "legal",
    "governance",
    "Runner_core",
    "adapter_boundaries",
)
POLICY_INVARIANT_PATTERNS: Final = (
    "BOOT_MANIFEST.yaml",
    "PROJECT_TREE.yaml",
    "OPERATOR_RULES.yaml",
    "CAPABILITY_REGISTRY.yaml",
    "core/gate_engine.py",
    "core/action_gate.py",
)


class ArchitectureImpact(Enum):
    LOW = "LOW"
    YELLOW = "YELLOW"
    PROTECTED = "PROTECTED"


@dataclass(frozen=True)
class ArchitectureInvariantEvidence:
    invariants_checked: bool
    immutable_invariants_preserved: bool
    dependency_boundaries_preserved: bool
    capability_surface_reviewed: bool
    policy_change: bool = False
    protected_review_required: bool = False
    bound_head_sha: str | None = None

    @property
    def is_green(self) -> bool:
        return (
            self.invariants_checked
            and self.immutable_invariants_preserved
            and self.dependency_boundaries_preserved
            and self.capability_surface_reviewed
        )


@dataclass(frozen=True)
class ArchitectureInvariantDecision:
    allowed: bool
    impact: ArchitectureImpact
    reason_codes: tuple[str, ...]
    protected_target_count: int
    policy_target_count: int
    touched_file_count: int
    touched_file_set_hash: str

    def public_receipt(self) -> dict[str, object]:
        return {
            "schema": ARCHITECTURE_INVARIANTS_SCHEMA,
            "allowed": self.allowed,
            "impact": self.impact.value,
            "reason_codes": self.reason_codes,
            "protected_target_count": self.protected_target_count,
            "policy_target_count": self.policy_target_count,
            "touched_file_count": self.touched_file_count,
            "touched_file_set_hash": self.touched_file_set_hash,
        }


def evaluate_architecture_invariants(
    *,
    touched_files: tuple[str, ...],
    declared_risk: str = "green",
    requested_capabilities: tuple[str, ...] = (),
    evidence: ArchitectureInvariantEvidence | None = None,
    review: ReviewEvidence | None = None,
    current_head_sha: str | None = None,
) -> ArchitectureInvariantDecision:
    reasons: list[str] = []
    safe_files = _safe_paths(touched_files)
    if safe_files is None:
        return _decision(
            ("INVALID_TOUCHED_FILES",),
            ArchitectureImpact.PROTECTED,
            0,
            0,
            touched_files,
        )
    if current_head_sha is not None and not is_full_sha(current_head_sha):
        reasons.append("INVALID_CURRENT_HEAD_SHA")

    protected = tuple(path for path in safe_files if is_protected_target(path))
    policy_targets = tuple(path for path in safe_files if is_policy_or_invariant_target(path))
    impact = classify_architecture_impact(
        touched_files=safe_files,
        declared_risk=declared_risk,
        requested_capabilities=requested_capabilities,
    )

    requires_architecture_evidence = impact is not ArchitectureImpact.LOW
    if requires_architecture_evidence and evidence is None:
        reasons.append("ARCHITECTURE_INVARIANT_EVIDENCE_REQUIRED")
    elif evidence is not None:
        if not evidence.is_green:
            reasons.append("ARCHITECTURE_INVARIANT_EVIDENCE_INCOMPLETE")
        if current_head_sha and evidence.bound_head_sha and evidence.bound_head_sha.lower() != current_head_sha.lower():
            reasons.append("ARCHITECTURE_EVIDENCE_SHA_MISMATCH")
        if current_head_sha and evidence.bound_head_sha is None and requires_architecture_evidence:
            reasons.append("ARCHITECTURE_EVIDENCE_SHA_REQUIRED")

    if requires_architecture_evidence:
        if review is None:
            reasons.append("INDEPENDENT_REVIEW_EVIDENCE_REQUIRED")
        elif not review.satisfies_architecture_review:
            reasons.append("INDEPENDENT_REVIEW_EVIDENCE_INCOMPLETE")
        elif current_head_sha and review.bound_head_sha and review.bound_head_sha.lower() != current_head_sha.lower():
            reasons.append("REVIEW_SHA_MISMATCH")
        elif current_head_sha and review.bound_head_sha is None:
            reasons.append("REVIEW_SHA_REQUIRED")

    policy_change_allowed = bool(evidence and evidence.policy_change and evidence.protected_review_required)
    if policy_targets and not policy_change_allowed:
        reasons.append("SELF_MODIFYING_POLICY_INVARIANT_BLOCKED")
    if protected and review is not None and not review.protected_review_required:
        reasons.append("PROTECTED_REVIEW_REQUIRED")

    return _decision(
        tuple(sorted(set(reasons))),
        impact,
        len(protected),
        len(policy_targets),
        safe_files,
    )


def classify_architecture_impact(
    *,
    touched_files: tuple[str, ...],
    declared_risk: str,
    requested_capabilities: tuple[str, ...] = (),
) -> ArchitectureImpact:
    risk = declared_risk.strip().lower() if isinstance(declared_risk, str) else ""
    if any(is_protected_target(path) for path in touched_files) or risk == "protected":
        return ArchitectureImpact.PROTECTED
    if risk in {"yellow", "high", "red"}:
        return ArchitectureImpact.YELLOW
    if any(capability in {"repository_maintenance", "publish_pull_request", "loop_control"} for capability in requested_capabilities):
        return ArchitectureImpact.YELLOW
    return ArchitectureImpact.LOW


def is_protected_target(path: str) -> bool:
    return any(path == pattern or path.startswith(pattern + "/") for pattern in PROTECTED_PATTERNS)


def is_policy_or_invariant_target(path: str) -> bool:
    return any(path == pattern or path.startswith(pattern + "/") for pattern in POLICY_INVARIANT_PATTERNS)


def _decision(
    reason_codes: tuple[str, ...],
    impact: ArchitectureImpact,
    protected_count: int,
    policy_count: int,
    touched_files: tuple[str, ...],
) -> ArchitectureInvariantDecision:
    return ArchitectureInvariantDecision(
        allowed=not reason_codes,
        impact=impact,
        reason_codes=reason_codes,
        protected_target_count=protected_count,
        policy_target_count=policy_count,
        touched_file_count=len(touched_files),
        touched_file_set_hash=stable_public_hash(touched_files),
    )


def _safe_paths(values: tuple[str, ...]) -> tuple[str, ...] | None:
    if not isinstance(values, tuple) or not values:
        return None
    if len(set(values)) != len(values):
        return None
    for value in values:
        if not isinstance(value, str) or not value or value.strip() != value:
            return None
        if value.startswith("/") or "\\" in value:
            return None
        if any(part in {"", ".", ".."} for part in value.split("/")):
            return None
    return tuple(sorted(values))
