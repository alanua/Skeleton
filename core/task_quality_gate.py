from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Final

from core.quality_evidence import EvidenceLevel, HeadBoundEvidence


_REPOSITORY_RE: Final = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_REF_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_SHA_RE: Final = re.compile(r"^[0-9a-fA-F]{40}$")
_BOUNDARY_TOKEN_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,95}$")
_IDEMPOTENCY_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/=-]{0,255}$")
_PATH_RE: Final = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._/@+*-]{0,511}$")


class TaskSpecValidationError(ValueError):
    """Raised when a current Runner task cannot be normalized safely."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class PrivacyBoundaryClass(str, Enum):
    PUBLIC_SAFE = "PUBLIC_SAFE"
    PROTECTED_PRIVATE = "PROTECTED_PRIVATE"


class AllowedScopeKind(str, Enum):
    EXACT_PATH = "EXACT_PATH"
    REPOSITORY_GLOB = "REPOSITORY_GLOB"


class DeclaredScopeProtection(str, Enum):
    PUBLIC_REVIEW_ALLOWED = "PUBLIC_REVIEW_ALLOWED"
    PROTECTED_REVIEW_REQUIRED = "PROTECTED_REVIEW_REQUIRED"


class GateStatus(str, Enum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAIL = "FAIL"


@dataclass(frozen=True)
class PrivacyBoundary:
    boundary_class: PrivacyBoundaryClass
    public_policy_tokens: tuple[str, ...]

    @classmethod
    def normalize(cls, value: object) -> PrivacyBoundary:
        tokens = _boundary_tokens(value)
        public_tokens = tuple(sorted(token for token in tokens if token.startswith("PUBLIC_SAFE_")))
        has_private = any(
            not token.startswith("PUBLIC_SAFE_")
            or "PRIVATE" in token
            or "LOCAL_ONLY" in token
            for token in tokens
        )
        return cls(
            boundary_class=(
                PrivacyBoundaryClass.PROTECTED_PRIVATE
                if has_private
                else PrivacyBoundaryClass.PUBLIC_SAFE
            ),
            public_policy_tokens=public_tokens,
        )

    def to_public_mapping(self) -> dict[str, object]:
        return {
            "boundary_class": self.boundary_class.value,
            "public_policy_tokens": list(self.public_policy_tokens),
        }


@dataclass(frozen=True)
class DeclaredAllowedScope:
    pattern: str
    kind: AllowedScopeKind

    @property
    def evidence_level(self) -> EvidenceLevel:
        return EvidenceLevel.DECLARED_ONLY


@dataclass(frozen=True)
class TaskSpec:
    repo: str
    base_ref: str
    head_ref: str
    task_kind: str
    payload: Mapping[str, Any]
    requested_capabilities: tuple[str, ...]
    allowed_scopes: tuple[DeclaredAllowedScope, ...]
    forbidden_actions: tuple[str, ...]
    validation: tuple[str, ...]
    expected_output: tuple[str, ...]
    privacy_boundary: PrivacyBoundary
    idempotency_key: str

    @classmethod
    def normalize(cls, value: Mapping[str, Any]) -> TaskSpec:
        if not isinstance(value, Mapping):
            raise TaskSpecValidationError(
                "INVALID_TASKSPEC",
                "task spec must be an object",
            )
        repo = _repository(_required(value, "repo"))
        base_ref = _ref(
            value.get("base_ref", value.get("base_branch", value.get("base_sha", "main"))),
            "base",
        )
        head_ref = _ref(value.get("head_ref", value.get("branch")), "head")
        return cls(
            repo=repo,
            base_ref=base_ref,
            head_ref=head_ref,
            task_kind=_plain_text(_required(value, "task_kind"), "task_kind"),
            payload=_freeze_json(value.get("payload", {})),
            requested_capabilities=_text_tuple(
                _required(value, "requested_capabilities"),
                "requested_capabilities",
                sort_items=True,
            ),
            allowed_scopes=_allowed_scopes(_required(value, "allowed_files")),
            forbidden_actions=_text_tuple(
                value.get("forbidden_actions", ()),
                "forbidden_actions",
                sort_items=True,
                allow_empty=True,
            ),
            validation=_text_tuple(
                value.get("validation", value.get("validation_commands", ())),
                "validation",
                sort_items=False,
                allow_empty=True,
            ),
            expected_output=_text_tuple(
                value.get("expected_output", ()),
                "expected_output",
                sort_items=True,
                allow_empty=True,
            ),
            privacy_boundary=PrivacyBoundary.normalize(_required(value, "privacy_boundary")),
            idempotency_key=_idempotency_key(_required(value, "idempotency_key")),
        )

    @property
    def declared_scope_protection(self) -> DeclaredScopeProtection:
        if self.privacy_boundary.boundary_class is PrivacyBoundaryClass.PUBLIC_SAFE:
            return DeclaredScopeProtection.PUBLIC_REVIEW_ALLOWED
        return DeclaredScopeProtection.PROTECTED_REVIEW_REQUIRED

    def public_normalized_mapping(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "task_kind": self.task_kind,
            "requested_capabilities": list(self.requested_capabilities),
            "allowed_scopes": [
                {
                    "pattern": scope.pattern,
                    "kind": scope.kind.value,
                    "evidence_level": scope.evidence_level.value,
                }
                for scope in self.allowed_scopes
            ],
            "declared_scope_protection": self.declared_scope_protection.value,
            "privacy_boundary": self.privacy_boundary.to_public_mapping(),
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True)
class Phase1GateConfig:
    architecture_required: bool = False
    production_contract_required: bool = False


@dataclass(frozen=True)
class Phase1GateResult:
    status: GateStatus
    evidence_level: EvidenceLevel
    protected_review_required: bool
    reasons: tuple[str, ...]


def evaluate_phase1_quality(
    *,
    task: TaskSpec,
    evidence: HeadBoundEvidence | None,
    head_sha: str,
    config: Phase1GateConfig | None = None,
    architecture_attestation: object = None,
    production_contract_attestation: object = None,
) -> Phase1GateResult:
    config = config or Phase1GateConfig()
    reasons: list[str] = []
    protected_review_required = (
        task.declared_scope_protection
        is DeclaredScopeProtection.PROTECTED_REVIEW_REQUIRED
    )
    evidence_level = EvidenceLevel.DECLARED_ONLY
    if protected_review_required:
        reasons.append("PROTECTED_DECLARED_SCOPE_REVIEW_REQUIRED")

    if evidence is None:
        reasons.append("MISSING_HEAD_BOUND_EVIDENCE")
    elif not evidence.is_valid_for_head(
        repo=task.repo,
        base_sha=task.base_ref,
        head_sha=_sha(head_sha, "head_sha"),
    ):
        reasons.append("HEAD_BOUND_EVIDENCE_INVALIDATED")
    elif not evidence.tests_passed:
        reasons.append("VALIDATION_FAILED")
    else:
        evidence_level = EvidenceLevel.HEAD_BOUND_VALIDATION

    if config.architecture_required:
        reasons.append("ARCHITECTURE_REVIEW_REQUIRED")
        protected_review_required = True
        evidence_level = EvidenceLevel.ARCHITECTURE_REVIEW_REQUIRED
    elif _claims_satisfied_gate(architecture_attestation):
        reasons.append("CALLER_ARCHITECTURE_ATTESTATION_IGNORED")

    if config.production_contract_required:
        reasons.append("PRODUCTION_CONTRACT_REVIEW_REQUIRED")
        protected_review_required = True
    elif _claims_satisfied_gate(production_contract_attestation):
        reasons.append("CALLER_PRODUCTION_CONTRACT_ATTESTATION_IGNORED")

    if reasons:
        status = (
            GateStatus.REVIEW_REQUIRED
            if protected_review_required
            or "ARCHITECTURE_REVIEW_REQUIRED" in reasons
            or "PRODUCTION_CONTRACT_REVIEW_REQUIRED" in reasons
            else GateStatus.FAIL
        )
    else:
        status = GateStatus.PASS

    if evidence_level in {EvidenceLevel.RUNTIME_PROVEN, EvidenceLevel.ARCHITECTURE_GREEN}:
        raise AssertionError("Phase 1 produced unreachable evidence")
    return Phase1GateResult(
        status=status,
        evidence_level=evidence_level,
        protected_review_required=protected_review_required,
        reasons=tuple(reasons),
    )


def _required(value: Mapping[str, Any], field: str) -> object:
    if field not in value:
        raise TaskSpecValidationError(
            f"MISSING_{field.upper()}",
            f"{field} is required",
        )
    return value[field]


def _repository(value: object) -> str:
    if not isinstance(value, str) or not _REPOSITORY_RE.fullmatch(value):
        raise TaskSpecValidationError(
            "INVALID_REPOSITORY",
            "repo must be owner/name",
        )
    return value


def _ref(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}_REF",
            f"{field} ref must be text",
        )
    if _SHA_RE.fullmatch(value):
        return value.lower()
    if not _REF_RE.fullmatch(value) or _has_path_escape(value):
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}_REF",
            f"{field} ref is malformed",
        )
    return value


def _plain_text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 128
        or _has_control(value)
    ):
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be bounded text",
        )
    return value.strip()


def _text_tuple(
    value: object,
    field: str,
    *,
    sort_items: bool,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be an array",
        )
    if not value and not allow_empty:
        raise TaskSpecValidationError(
            f"EMPTY_{field.upper()}",
            f"{field} must not be empty",
        )
    normalized = tuple(_plain_text(item, field) for item in value)
    if len(set(normalized)) != len(normalized):
        raise TaskSpecValidationError(
            f"DUPLICATE_{field.upper()}",
            f"{field} contains duplicates",
        )
    return tuple(sorted(normalized)) if sort_items else normalized


def _allowed_scopes(value: object) -> tuple[DeclaredAllowedScope, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TaskSpecValidationError(
            "INVALID_ALLOWED_FILE_SCOPE",
            "allowed scope must be an array",
        )
    if not value:
        raise TaskSpecValidationError(
            "INVALID_ALLOWED_FILE_SCOPE",
            "allowed scope must not be empty",
        )
    scopes: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TaskSpecValidationError(
                "INVALID_ALLOWED_FILE_SCOPE",
                "allowed scope must be text",
            )
        scopes.append(item)
    if len(set(scopes)) != len(scopes):
        raise TaskSpecValidationError(
            "DUPLICATE_ALLOWED_FILE_SCOPE",
            "allowed scope contains duplicates",
        )
    scopes.sort()
    normalized: list[DeclaredAllowedScope] = []
    for scope in scopes:
        normalized.append(DeclaredAllowedScope(pattern=scope, kind=_scope_kind(scope)))
    return tuple(normalized)


def _scope_kind(value: str) -> AllowedScopeKind:
    if not _PATH_RE.fullmatch(value) or _has_control(value):
        raise TaskSpecValidationError(
            "INVALID_ALLOWED_FILE_SCOPE",
            "allowed scope must be repository-relative",
        )
    if (
        value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "//" in value
        or _has_path_escape(value)
    ):
        raise TaskSpecValidationError(
            "INVALID_ALLOWED_FILE_SCOPE",
            "allowed scope must not escape repository scope",
        )
    if "*" not in value:
        return AllowedScopeKind.EXACT_PATH
    if value in {"*", "**", "*/**"} or value.startswith("*"):
        raise TaskSpecValidationError(
            "INVALID_ALLOWED_FILE_SCOPE",
            "wildcard allowed scope must be bounded by a directory prefix",
        )
    if value.endswith("/**") and "*" not in value[:-3]:
        return AllowedScopeKind.REPOSITORY_GLOB
    raise TaskSpecValidationError(
        "INVALID_ALLOWED_FILE_SCOPE",
        "only bounded repository-relative /** globs are accepted",
    )


def _boundary_tokens(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = re.split(r"\s*/\s*|\s*,\s*|\s*\+\s*", value.strip())
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts = list(value)
    else:
        raise TaskSpecValidationError(
            "INVALID_PRIVACY_BOUNDARY",
            "privacy boundary must be text or an array of tokens",
        )
    tokens: list[str] = []
    for part in parts:
        if (
            not isinstance(part, str)
            or not part
            or _has_control(part)
            or not _BOUNDARY_TOKEN_RE.fullmatch(part)
        ):
            raise TaskSpecValidationError(
                "INVALID_PRIVACY_BOUNDARY",
                "privacy boundary token is malformed",
            )
        tokens.append(part)
    if not tokens:
        raise TaskSpecValidationError(
            "INVALID_PRIVACY_BOUNDARY",
            "privacy boundary must not be empty",
        )
    return tuple(sorted(set(tokens)))


def _idempotency_key(value: object) -> str:
    if (
        not isinstance(value, str)
        or not _IDEMPOTENCY_RE.fullmatch(value)
        or _has_control(value)
        or _has_path_escape(value)
        or any(part.startswith("/") for part in value.split(":"))
        or value.startswith("/")
        or "\\" in value
        or "//" in value
    ):
        raise TaskSpecValidationError(
            "INVALID_IDEMPOTENCY_KEY",
            "idempotency_key must be a bounded non-traversing token",
        )
    return value


def _freeze_json(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(value[key]) for key in sorted(value)})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item) for item in value)
    raise TaskSpecValidationError(
        "INVALID_PAYLOAD",
        "payload must be JSON-compatible",
    )


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise TaskSpecValidationError(
            f"INVALID_{field.upper()}",
            f"{field} must be a 40-character commit SHA",
        )
    return value.lower()


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _has_path_escape(value: str) -> bool:
    return (
        value in {".", ".."}
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "@{" in value
        or any(segment in {"", ".", ".."} for segment in value.split("/"))
    )


def _claims_satisfied_gate(value: object) -> bool:
    return isinstance(value, (bool, str, list, tuple, dict)) and bool(value)
