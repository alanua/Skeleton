from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Final


_SHA_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_RE: Final = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)


class ArchitectureStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    GREEN = "ARCHITECTURE_GREEN"
    BLOCKED = "ARCHITECTURE_BLOCKED"


class EvidenceKind(str, Enum):
    REAL = "REAL"
    SANDBOX = "SANDBOX"
    STAGING = "STAGING"
    MOCK = "MOCK"
    STATIC_REVIEW = "STATIC_REVIEW"


@dataclass(frozen=True)
class EvidenceBinding:
    repository: str
    base_sha: str
    head_sha: str


@dataclass(frozen=True)
class ArchitectureEvidence:
    binding: EvidenceBinding
    kind: EvidenceKind
    invariant_ids: tuple[str, ...]
    passed: bool
    reviewer_id: str


@dataclass(frozen=True)
class ArchitectureInvariantResult:
    status: ArchitectureStatus
    reason_codes: tuple[str, ...]
    binding: EvidenceBinding | None = None
    invariant_count: int = 0

    @property
    def green(self) -> bool:
        return self.status is ArchitectureStatus.GREEN


def evaluate_architecture_invariants(
    *,
    required: bool,
    evidence: ArchitectureEvidence | None,
    repository: str,
    base_sha: str,
    head_sha: str,
    current_head_sha: str | None = None,
    allow_static_review: bool = True,
) -> ArchitectureInvariantResult:
    reasons: list[str] = []
    normalized_repo = _repository(repository, reasons, "INVALID_REPOSITORY")
    normalized_base = _sha(base_sha, reasons, "INVALID_BASE_SHA")
    normalized_head = _sha(head_sha, reasons, "INVALID_HEAD_SHA")
    normalized_current = (
        _sha(current_head_sha, reasons, "INVALID_CURRENT_HEAD_SHA")
        if current_head_sha is not None
        else normalized_head
    )
    if reasons:
        return ArchitectureInvariantResult(
            status=ArchitectureStatus.BLOCKED,
            reason_codes=tuple(reasons),
        )
    if not required:
        return ArchitectureInvariantResult(
            status=ArchitectureStatus.NOT_REQUIRED,
            reason_codes=(),
        )
    if evidence is None:
        return ArchitectureInvariantResult(
            status=ArchitectureStatus.BLOCKED,
            reason_codes=("MISSING_ARCHITECTURE_EVIDENCE",),
        )
    if not isinstance(evidence, ArchitectureEvidence):
        return ArchitectureInvariantResult(
            status=ArchitectureStatus.BLOCKED,
            reason_codes=("MALFORMED_ARCHITECTURE_EVIDENCE",),
        )

    if evidence.binding.repository != normalized_repo:
        reasons.append("EVIDENCE_REPOSITORY_MISMATCH")
    if evidence.binding.base_sha != normalized_base:
        reasons.append("EVIDENCE_BASE_SHA_MISMATCH")
    if evidence.binding.head_sha != normalized_head:
        reasons.append("EVIDENCE_HEAD_SHA_MISMATCH")
    if normalized_current != normalized_head:
        reasons.append("HEAD_MOVED")
    if evidence.kind is EvidenceKind.MOCK:
        reasons.append("MOCK_ARCHITECTURE_EVIDENCE")
    if evidence.kind is EvidenceKind.STATIC_REVIEW and not allow_static_review:
        reasons.append("STATIC_REVIEW_NOT_ALLOWED")
    if not evidence.invariant_ids:
        reasons.append("MISSING_INVARIANT_IDS")
    if not evidence.reviewer_id:
        reasons.append("MISSING_REVIEWER_ID")
    if not evidence.passed:
        reasons.append("ARCHITECTURE_INVARIANTS_FAILED")

    if reasons:
        return ArchitectureInvariantResult(
            status=ArchitectureStatus.BLOCKED,
            reason_codes=tuple(dict.fromkeys(reasons)),
            binding=evidence.binding,
            invariant_count=len(evidence.invariant_ids),
        )
    return ArchitectureInvariantResult(
        status=ArchitectureStatus.GREEN,
        reason_codes=(),
        binding=evidence.binding,
        invariant_count=len(evidence.invariant_ids),
    )


def public_receipt(result: ArchitectureInvariantResult) -> MappingProxyType[str, object]:
    return MappingProxyType(
        {
            "status": result.status.value,
            "reason_codes": result.reason_codes,
            "invariant_count": result.invariant_count,
            "base_sha": result.binding.base_sha if result.binding else None,
            "head_sha": result.binding.head_sha if result.binding else None,
        }
    )


def _repository(value: object, reasons: list[str], code: str) -> str | None:
    if not isinstance(value, str) or not _REPOSITORY_RE.fullmatch(value):
        reasons.append(code)
        return None
    return value


def _sha(value: object, reasons: list[str], code: str) -> str | None:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        reasons.append(code)
        return None
    return value.lower()
