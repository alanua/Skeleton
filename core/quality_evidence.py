from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any, Final


PUBLIC_PRIVACY_BOUNDARIES: Final = frozenset(
    {
        "PUBLIC_SAFE",
        "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "PUBLIC_SAFE_REPOSITORY_ONLY",
        "PUBLIC_SAFE_AGGREGATE_ONLY",
    }
)
PRIVATE_PRIVACY_BOUNDARIES: Final = frozenset(
    {
        "PRIVATE",
        "PRIVATE_LOCAL",
        "LOCAL_PRIVATE",
        "PROTECTED_PRIVATE",
        "COMPOSITE_PRIVATE_PUBLIC_SAFE",
        "COMPOSITE_PRIVATE_PUBLIC_SAFE_BOUNDARY",
    }
)
SUPPORTED_PRIVACY_BOUNDARIES: Final = PUBLIC_PRIVACY_BOUNDARIES | PRIVATE_PRIVACY_BOUNDARIES
RISK_ORDER: Final = {
    "GREEN": 0,
    "LOW": 0,
    "MEDIUM": 1,
    "YELLOW": 1,
    "HIGH": 2,
    "RED": 2,
    "CRITICAL": 3,
    "PROTECTED": 3,
}
NORMALIZED_RISK: Final = {
    "GREEN": "LOW",
    "LOW": "LOW",
    "MEDIUM": "MEDIUM",
    "YELLOW": "MEDIUM",
    "HIGH": "HIGH",
    "RED": "HIGH",
    "CRITICAL": "CRITICAL",
    "PROTECTED": "PROTECTED",
}
ALLOWED_EVIDENCE_STATES: Final = frozenset(
    {
        "ABSENT",
        "DECLARED",
        "HEAD_BOUND",
        "REVIEWED",
        "INVALIDATED",
    }
)

_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


class QualityEvidenceError(ValueError):
    """Raised when claim-side task quality evidence is malformed."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class EvidenceReceipt:
    evidence_type: str
    state: str = "DECLARED"
    head_sha: str | None = None

    @classmethod
    def from_mapping(cls, value: object) -> "EvidenceReceipt":
        if not isinstance(value, Mapping):
            raise QualityEvidenceError(
                "INVALID_EVIDENCE_RECEIPT",
                "evidence receipt must be an object",
            )
        evidence_type = _non_empty_string(value.get("evidence_type"), "evidence_type")
        state = _normalize_evidence_state(value.get("state", "DECLARED"))
        head_sha = _optional_sha(value.get("head_sha"))
        return cls(evidence_type=evidence_type, state=state, head_sha=head_sha)

    def bind_to_head(self, current_head_sha: str | None) -> "EvidenceReceipt":
        if self.head_sha is None or current_head_sha is None:
            return self
        if self.head_sha.lower() == current_head_sha.lower():
            return self
        return EvidenceReceipt(
            evidence_type=self.evidence_type,
            state="INVALIDATED",
            head_sha=self.head_sha,
        )

    def public_mapping(self) -> dict[str, str]:
        result = {"evidence_type": self.evidence_type, "state": self.state}
        if self.head_sha:
            result["head_sha"] = self.head_sha.lower()
        return result


@dataclass(frozen=True)
class TaskSpec:
    repo: str
    idempotency_key: str
    privacy_boundary: str
    normalized_risk: str
    protected_intent: bool
    declared_exact_paths: tuple[str, ...] = ()
    declared_globs: tuple[str, ...] = ()
    evidence_receipts: tuple[EvidenceReceipt, ...] = ()
    raw_private_claim_present: bool = False

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any]) -> "TaskSpec":
        if not isinstance(claims, Mapping):
            raise QualityEvidenceError("INVALID_TASK_SPEC", "task spec must be an object")
        repo = _repository(claims.get("repo"))
        idempotency_key = _idempotency_key(claims.get("idempotency_key"), repo)
        privacy_boundary = _privacy_boundary(claims.get("privacy_boundary", "PUBLIC_SAFE"))
        normalized_risk = normalize_risk(
            claims.get(
                "risk",
                claims.get("risk_level", claims.get("quality_risk", "LOW")),
            )
        )
        protected_intent = _explicit_bool(claims.get("protected_intent", False))

        exact_paths: list[str] = []
        globs: list[str] = []
        for scope in _declared_scopes(claims):
            normalized, kind = normalize_repository_scope(scope)
            if kind == "glob":
                globs.append(normalized)
            else:
                exact_paths.append(normalized)

        receipts = tuple(
            EvidenceReceipt.from_mapping(receipt)
            for receipt in _optional_sequence(claims.get("evidence_receipts", ()))
        )
        raw_private_claim_present = any(
            key in claims
            for key in (
                "private_claims",
                "private_data",
                "raw_private_values",
                "private_values",
            )
        )
        return cls(
            repo=repo,
            idempotency_key=idempotency_key,
            privacy_boundary=privacy_boundary,
            normalized_risk=normalized_risk,
            protected_intent=protected_intent,
            declared_exact_paths=tuple(sorted(set(exact_paths))),
            declared_globs=tuple(sorted(set(globs))),
            evidence_receipts=receipts,
            raw_private_claim_present=raw_private_claim_present,
        )

    @property
    def private_or_composite_boundary(self) -> bool:
        return self.privacy_boundary in PRIVATE_PRIVACY_BOUNDARIES

    def bind_evidence_to_head(self, current_head_sha: str | None) -> "TaskSpec":
        return TaskSpec(
            repo=self.repo,
            idempotency_key=self.idempotency_key,
            privacy_boundary=self.privacy_boundary,
            normalized_risk=self.normalized_risk,
            protected_intent=self.protected_intent,
            declared_exact_paths=self.declared_exact_paths,
            declared_globs=self.declared_globs,
            evidence_receipts=tuple(
                receipt.bind_to_head(current_head_sha) for receipt in self.evidence_receipts
            ),
            raw_private_claim_present=self.raw_private_claim_present,
        )

    def public_mapping(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "idempotency_key": self.idempotency_key,
            "privacy_boundary": self.privacy_boundary,
            "normalized_risk": self.normalized_risk,
            "protected_intent": self.protected_intent,
            "declared_exact_paths": list(self.declared_exact_paths),
            "declared_globs": list(self.declared_globs),
            "evidence_receipts": [
                receipt.public_mapping() for receipt in self.evidence_receipts
            ],
            "raw_private_claim_present": self.raw_private_claim_present,
        }


def normalize_risk(value: object) -> str:
    if value is None:
        return "LOW"
    if not isinstance(value, str):
        raise QualityEvidenceError("INVALID_RISK", "risk must be a string")
    normalized = value.strip().replace("-", "_").replace(" ", "_").upper()
    if normalized not in NORMALIZED_RISK:
        raise QualityEvidenceError("INVALID_RISK", f"unsupported risk: {value!r}")
    return NORMALIZED_RISK[normalized]


def risk_at_least(value: str, threshold: str) -> bool:
    return RISK_ORDER[normalize_risk(value)] >= RISK_ORDER[normalize_risk(threshold)]


def normalize_repository_scope(value: object) -> tuple[str, str]:
    scope = _non_empty_string(value, "declared_scope").replace("\\", "/")
    if any(ord(character) < 32 or ord(character) == 127 for character in scope):
        raise QualityEvidenceError("INVALID_SCOPE", "scope contains a control character")
    if scope.startswith("/") or scope.startswith("~"):
        raise QualityEvidenceError("INVALID_SCOPE", "scope must be repository-relative")
    parts = scope.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise QualityEvidenceError("INVALID_SCOPE", "scope must not traverse directories")
    if scope in {"*", "**", "**/*", "*/**"}:
        raise QualityEvidenceError("INVALID_SCOPE", "scope wildcard is unbounded")
    if scope.startswith("**/") or "/**/" in scope:
        raise QualityEvidenceError("INVALID_SCOPE", "glob must be anchored to a repository path")
    if scope.endswith("/**") and len(parts) >= 2:
        return scope, "glob"
    if any(character in scope for character in "*?[]"):
        raise QualityEvidenceError("INVALID_SCOPE", "only bounded path/** globs are supported")
    return scope, "exact"


def _declared_scopes(claims: Mapping[str, Any]) -> tuple[object, ...]:
    scopes: list[object] = []
    for key in (
        "allowed_files",
        "allowed_scopes",
        "declared_paths",
        "declared_scopes",
        "declared_path_globs",
    ):
        value = claims.get(key)
        if value is not None:
            scopes.extend(_optional_sequence(value))
    return tuple(scopes)


def _repository(value: object) -> str:
    repo = _non_empty_string(value, "repo")
    if not _REPOSITORY_RE.fullmatch(repo):
        raise QualityEvidenceError("INVALID_REPOSITORY", "repo must be owner/name")
    return repo


def _idempotency_key(value: object, repo: str) -> str:
    key = _non_empty_string(value, "idempotency_key")
    if not _IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise QualityEvidenceError(
            "INVALID_IDEMPOTENCY_KEY",
            "idempotency key contains unsupported characters",
        )
    if f":{repo}:" not in f":{key}:":
        raise QualityEvidenceError(
            "INVALID_IDEMPOTENCY_KEY",
            "idempotency key must contain the repository identity",
        )
    return key


def _privacy_boundary(value: object) -> str:
    boundary = _non_empty_string(value, "privacy_boundary").strip().upper()
    if boundary not in SUPPORTED_PRIVACY_BOUNDARIES:
        raise QualityEvidenceError(
            "INVALID_PRIVACY_BOUNDARY",
            f"unsupported privacy boundary: {value!r}",
        )
    return boundary


def _normalize_evidence_state(value: object) -> str:
    state = _non_empty_string(value, "state").strip().upper()
    if state == "RUNTIME_PROVEN":
        return "HEAD_BOUND"
    if state not in ALLOWED_EVIDENCE_STATES:
        raise QualityEvidenceError(
            "INVALID_EVIDENCE_STATE",
            f"unsupported evidence state: {value!r}",
        )
    return state


def _optional_sha(value: object) -> str | None:
    if value is None:
        return None
    sha = _non_empty_string(value, "head_sha").lower()
    if not _SHA_RE.fullmatch(sha):
        raise QualityEvidenceError("INVALID_HEAD_SHA", "head SHA must be 40 hex chars")
    return sha


def _explicit_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "protected"}
    return False


def _optional_sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise QualityEvidenceError("INVALID_SEQUENCE", "value must be a sequence")
    return tuple(value)


def _non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualityEvidenceError("INVALID_FIELD", f"{field} must be a non-empty string")
    return value.strip()
