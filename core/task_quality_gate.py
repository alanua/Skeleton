from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Final

from core.quality_evidence import (
    QualityEvidenceError,
    TaskSpec,
    normalize_repository_scope,
    risk_at_least,
)


PROTECTED_EXACT_PATHS: Final = frozenset(
    {
        "BOOT_MANIFEST.yaml",
        "PROJECT_TREE.yaml",
        "OPERATOR_RULES.yaml",
        "CAPABILITY_REGISTRY.yaml",
        ".github/workflows",
        "scripts/runner_poll_github_tasks.py",
        "core/gate_engine.py",
        "core/action_gate.py",
    }
)
PROTECTED_PREFIXES: Final = (
    ".github/workflows/",
    "secrets/",
    "deploy/",
    "server/",
    "finance/",
    "legal/",
    "governance/",
    "Runner_core/",
    "adapter_boundaries/",
    "core/runner_",
)
PROTECTED_GLOBS: Final = frozenset(
    {
        ".github/workflows/**",
        "scripts/runner_poll_github_tasks.py",
        "core/gate_engine.py",
        "core/action_gate.py",
    }
)
PROTECTED_RISKS: Final = frozenset({"HIGH", "CRITICAL", "PROTECTED"})
ARCHITECTURE_RECEIPT_TYPES: Final = frozenset(
    {"architecture_review", "architecture_receipt"}
)
PRODUCTION_CONTRACT_RECEIPT_TYPES: Final = frozenset(
    {"production_contract", "production_contract_receipt"}
)


@dataclass(frozen=True)
class ProtectedScopeClassification:
    declared_path: str
    scope_kind: str
    protected: bool
    reason: str | None = None

    def public_mapping(self) -> dict[str, str | bool]:
        result: dict[str, str | bool] = {
            "declared_path": self.declared_path,
            "scope_kind": self.scope_kind,
            "protected": self.protected,
        }
        if self.reason:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True)
class TaskQualityGateDecision:
    status: str
    protected_review_required: bool
    public_review_allowed: bool
    reason_codes: tuple[str, ...]
    scope_classifications: tuple[ProtectedScopeClassification, ...]
    task_spec: TaskSpec

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"

    def public_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "protected_review_required": self.protected_review_required,
            "public_review_allowed": self.public_review_allowed,
            "reason_codes": list(self.reason_codes),
            "task_spec": self.task_spec.public_mapping(),
            "scope_classifications": [
                classification.public_mapping()
                for classification in self.scope_classifications
            ],
        }


def evaluate_task_quality_gate(
    claims: Mapping[str, Any] | TaskSpec,
    *,
    current_head_sha: str | None = None,
    architecture_required: bool = False,
    production_contract_required: bool = False,
    protected_risks: set[str] | frozenset[str] = PROTECTED_RISKS,
) -> TaskQualityGateDecision:
    task_spec = claims if isinstance(claims, TaskSpec) else TaskSpec.from_claims(claims)
    task_spec = task_spec.bind_evidence_to_head(current_head_sha)

    classifications = classify_protected_declared_scopes(task_spec)
    reason_codes: list[str] = []

    if task_spec.private_or_composite_boundary:
        reason_codes.append("PRIVATE_OR_COMPOSITE_PRIVACY_BOUNDARY")
    if task_spec.protected_intent:
        reason_codes.append("EXPLICIT_PROTECTED_INTENT")
    if any(classification.protected for classification in classifications):
        reason_codes.append("PROTECTED_DECLARED_SCOPE")

    normalized_protected_risks = {risk.upper() for risk in protected_risks}
    if task_spec.normalized_risk in normalized_protected_risks or risk_at_least(
        task_spec.normalized_risk, "HIGH"
    ):
        reason_codes.append("PROTECTED_RISK")

    invalidated = any(
        receipt.state == "INVALIDATED" for receipt in task_spec.evidence_receipts
    )
    if invalidated:
        reason_codes.append("HEAD_BOUND_EVIDENCE_INVALIDATED")

    if architecture_required and not _has_reviewed_receipt(
        task_spec, ARCHITECTURE_RECEIPT_TYPES
    ):
        reason_codes.append("ARCHITECTURE_REVIEW_REQUIRED")
    if production_contract_required and not _has_reviewed_receipt(
        task_spec, PRODUCTION_CONTRACT_RECEIPT_TYPES
    ):
        reason_codes.append("PRODUCTION_CONTRACT_REVIEW_REQUIRED")

    protected_review_required = any(
        code
        in {
            "PRIVATE_OR_COMPOSITE_PRIVACY_BOUNDARY",
            "EXPLICIT_PROTECTED_INTENT",
            "PROTECTED_DECLARED_SCOPE",
            "PROTECTED_RISK",
            "HEAD_BOUND_EVIDENCE_INVALIDATED",
            "ARCHITECTURE_REVIEW_REQUIRED",
            "PRODUCTION_CONTRACT_REVIEW_REQUIRED",
        }
        for code in reason_codes
    )
    status = "protected_review_required" if protected_review_required else "allowed"
    return TaskQualityGateDecision(
        status=status,
        protected_review_required=protected_review_required,
        public_review_allowed=not protected_review_required,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        scope_classifications=classifications,
        task_spec=task_spec,
    )


def classify_protected_declared_scopes(
    task_spec: TaskSpec,
) -> tuple[ProtectedScopeClassification, ...]:
    classifications: list[ProtectedScopeClassification] = []
    for path in task_spec.declared_exact_paths:
        protected, reason = _path_is_protected(path)
        classifications.append(
            ProtectedScopeClassification(
                declared_path=path,
                scope_kind="exact",
                protected=protected,
                reason=reason,
            )
        )
    for glob in task_spec.declared_globs:
        protected, reason = _glob_is_protected(glob)
        classifications.append(
            ProtectedScopeClassification(
                declared_path=glob,
                scope_kind="glob",
                protected=protected,
                reason=reason,
            )
        )
    return tuple(classifications)


def classify_scope(value: object) -> ProtectedScopeClassification:
    normalized, kind = normalize_repository_scope(value)
    protected, reason = (
        _glob_is_protected(normalized) if kind == "glob" else _path_is_protected(normalized)
    )
    return ProtectedScopeClassification(
        declared_path=normalized,
        scope_kind=kind,
        protected=protected,
        reason=reason,
    )


def _path_is_protected(path: str) -> tuple[bool, str | None]:
    if path in PROTECTED_EXACT_PATHS:
        return True, "protected_exact_path"
    if any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return True, "protected_prefix"
    return False, None


def _glob_is_protected(glob: str) -> tuple[bool, str | None]:
    if glob in PROTECTED_GLOBS:
        return True, "protected_glob"
    prefix = glob.removesuffix("/**")
    protected, reason = _path_is_protected(prefix)
    if protected:
        return True, reason
    for protected_path in PROTECTED_EXACT_PATHS:
        if fnmatchcase(protected_path, glob):
            return True, "protected_glob_intersects_exact_path"
    for protected_prefix in PROTECTED_PREFIXES:
        sample = protected_prefix.rstrip("/") + "/sample"
        if fnmatchcase(sample, glob):
            return True, "protected_glob_intersects_prefix"
    return False, None


def _has_reviewed_receipt(task_spec: TaskSpec, receipt_types: frozenset[str]) -> bool:
    return any(
        receipt.evidence_type in receipt_types and receipt.state == "REVIEWED"
        for receipt in task_spec.evidence_receipts
    )


__all__ = [
    "QualityEvidenceError",
    "TaskQualityGateDecision",
    "ProtectedScopeClassification",
    "TaskSpec",
    "classify_protected_declared_scopes",
    "classify_scope",
    "evaluate_task_quality_gate",
]
