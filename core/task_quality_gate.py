from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import fnmatch
import re
from types import MappingProxyType
from typing import Any, Final

from core.quality_evidence import Phase1EvidenceDecision, evaluate_phase1_evidence


PUBLIC_REVIEW_ALLOWED: Final = "PUBLIC_REVIEW_ALLOWED"
REVIEW_RELEVANT: Final = "REVIEW_RELEVANT"
PROTECTED_REVIEW_REQUIRED: Final = "PROTECTED_REVIEW_REQUIRED"
PRIVATE_PROTECTED: Final = "PRIVATE_PROTECTED"
PUBLIC_SAFE: Final = "PUBLIC_SAFE"
UNKNOWN_BOUNDARY: Final = "UNKNOWN_BOUNDARY"

_RISK_ALIASES: Final = {
    "green": "green",
    "low": "green",
    "yellow": "yellow",
    "medium": "yellow",
    "red": "red",
    "high": "red",
    "critical": "critical",
    "protected": "critical",
}
_PROTECTED_RISKS: Final = frozenset({"red", "critical"})
_PUBLIC_SAFE_PRIVACY_BOUNDARIES: Final = frozenset(
    {
        "PUBLIC_SAFE_AGGREGATE_ONLY",
        "PUBLIC_SAFE_CODE_AND_SYNTHETIC_TESTS_ONLY",
        "PUBLIC_SAFE_HASH_STATUS_ONLY",
        "PUBLIC_SAFE_METADATA_ONLY",
        "PUBLIC_SAFE_POLICY_METADATA_ONLY",
        "PUBLIC_SAFE_QUEUE_AND_PR_METADATA_ONLY",
        "PUBLIC_SAFE_QUEUE_METADATA_ONLY",
        "PUBLIC_SAFE_READ_ONLY",
        "PUBLIC_SAFE_REPOSITORY_ONLY",
        "PUBLIC_SAFE_SOURCE_AND_SYNTHETIC_TESTS_ONLY",
        "PUBLIC_SAFE_SYNTHETIC_ONLY",
    }
)
_PRIVATE_MARKERS: Final = (
    "CREDENTIAL",
    "LOCAL",
    "NODE_IDENTITY",
    "PERSONAL",
    "PRIVATE",
    "PRIVILEGE",
    "PROTECTED",
    "RESTRICTED",
    "RUNTIME",
    "SECRET",
)
_SUPPORTED_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "allowed_files",
        "base",
        "base_sha",
        "branch",
        "expected_output",
        "forbidden_actions",
        "idempotency",
        "idempotency_key",
        "payload",
        "privacy",
        "privacy_boundary",
        "project",
        "repo",
        "requested_capabilities",
        "required_tests",
        "risk",
        "risk_level",
        "schema",
        "task_kind",
        "validation",
    }
)
_PROTECTED_SURFACE_EXACT: Final = frozenset(
    {
        "BOOT_MANIFEST.yaml",
        "CAPABILITY_REGISTRY.yaml",
        "OPERATOR_RULES.yaml",
        "PROJECT_TREE.yaml",
        "core/action_gate.py",
        "core/gate_engine.py",
        "scripts/runner_poll_github_tasks.py",
    }
)
_PROTECTED_SURFACE_GLOBS: Final = (
    ".github/workflows/**",
    "adapters/**/SYSTEM_PROMPT.md",
    "adapter_boundaries/**",
    "deploy/**",
    "finance/**",
    "governance/**",
    "legal/**",
    "Runner_core/**",
    "secrets/**",
    "server/**",
)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+ -]{0,511}$")


class TaskQualityGateError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class NormalizedRisk:
    raw: str
    canonical: str
    review_class: str
    protected: bool


@dataclass(frozen=True)
class NormalizedPrivacyBoundary:
    privacy_class: str
    protected: bool
    public_safe_portions: tuple[str, ...]
    private_portions: tuple[str, ...]


@dataclass(frozen=True)
class Phase1Task:
    schema: str
    repo: str
    base: str | None
    base_sha: str
    branch: str
    task_kind: str
    payload: Mapping[str, Any]
    requested_capabilities: tuple[str, ...]
    allowed_files: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    validation: tuple[str, ...]
    required_tests: tuple[str, ...]
    expected_output: tuple[str, ...]
    idempotency_key: str
    project: str | None
    risk: NormalizedRisk
    privacy_boundary: NormalizedPrivacyBoundary
    extensions: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Phase1Task":
        if not isinstance(value, Mapping):
            raise TaskQualityGateError("INVALID_TASK", "task must be an object")
        unknown = sorted(set(value) - _SUPPORTED_TOP_LEVEL_FIELDS)
        if unknown:
            raise TaskQualityGateError(
                "UNKNOWN_UNSUPPORTED_FIELD",
                f"unsupported task field: {unknown[0]}",
            )

        risk = normalize_risk(_coalesced_alias(value, "risk", "risk_level"))
        privacy_raw = _coalesced_alias(value, "privacy_boundary", "privacy")
        privacy_boundary = normalize_privacy_boundary(privacy_raw)
        if privacy_boundary.privacy_class == UNKNOWN_BOUNDARY:
            raise TaskQualityGateError(
                "UNKNOWN_PRIVACY_BOUNDARY",
                "privacy boundary is not supported by Phase 1",
            )

        payload = _mapping(value.get("payload", {}), "payload")
        return cls(
            schema=_string(value.get("schema"), "schema"),
            repo=_string(value.get("repo"), "repo"),
            base=_optional_string(value.get("base"), "base"),
            base_sha=_string(value.get("base_sha"), "base_sha"),
            branch=_string(value.get("branch"), "branch"),
            task_kind=_string(value.get("task_kind"), "task_kind"),
            payload=payload,
            requested_capabilities=_string_tuple(
                value.get("requested_capabilities"), "requested_capabilities"
            ),
            allowed_files=_string_tuple(value.get("allowed_files"), "allowed_files"),
            forbidden_actions=_string_tuple(
                value.get("forbidden_actions"), "forbidden_actions"
            ),
            validation=_string_tuple(value.get("validation"), "validation"),
            required_tests=_optional_string_tuple(
                value.get("required_tests"), "required_tests"
            ),
            expected_output=_string_tuple(value.get("expected_output"), "expected_output"),
            idempotency_key=_string(
                value.get("idempotency_key", value.get("idempotency")), "idempotency_key"
            ),
            project=_optional_string(value.get("project"), "project"),
            risk=risk,
            privacy_boundary=privacy_boundary,
            extensions=MappingProxyType(
                {
                    key: _freeze(value[key])
                    for key in ("base", "project", "required_tests")
                    if key in value
                }
            ),
        )

    @property
    def protected(self) -> bool:
        return self.risk.protected or self.privacy_boundary.protected

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "repo": self.repo,
            "base": self.base,
            "base_sha": self.base_sha,
            "branch": self.branch,
            "task_kind": self.task_kind,
            "payload": _thaw(self.payload),
            "requested_capabilities": list(self.requested_capabilities),
            "allowed_files": list(self.allowed_files),
            "forbidden_actions": list(self.forbidden_actions),
            "validation": list(self.validation),
            "required_tests": list(self.required_tests),
            "expected_output": list(self.expected_output),
            "idempotency_key": self.idempotency_key,
            "project": self.project,
            "risk": {
                "canonical": self.risk.canonical,
                "review_class": self.risk.review_class,
                "protected": self.risk.protected,
            },
            "privacy_boundary": {
                "privacy_class": self.privacy_boundary.privacy_class,
                "protected": self.privacy_boundary.protected,
                "public_safe_portions": list(self.privacy_boundary.public_safe_portions),
                "private_portions": list(self.privacy_boundary.private_portions),
            },
            "extensions": _thaw(self.extensions),
        }


@dataclass(frozen=True)
class TaskQualityGateDecision:
    status: str
    task: Phase1Task
    protected_surfaces: tuple[str, ...]
    evidence: Phase1EvidenceDecision
    reason_codes: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return self.status == "public_review_allowed"

    @property
    def protected(self) -> bool:
        return self.status == "protected_review_required"

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "protected_surfaces": list(self.protected_surfaces),
            "evidence": self.evidence.to_mapping(),
            "reason_codes": list(self.reason_codes),
            "task": self.task.to_mapping(),
        }


def normalize_risk(value: object) -> NormalizedRisk:
    raw = _string(value, "risk").strip().lower()
    canonical = _RISK_ALIASES.get(raw)
    if canonical is None:
        raise TaskQualityGateError("UNKNOWN_RISK", "risk is not supported by Phase 1")
    if canonical in _PROTECTED_RISKS:
        return NormalizedRisk(raw=raw, canonical=canonical, review_class=PROTECTED_REVIEW_REQUIRED, protected=True)
    if canonical == "yellow":
        return NormalizedRisk(raw=raw, canonical=canonical, review_class=REVIEW_RELEVANT, protected=False)
    return NormalizedRisk(raw=raw, canonical=canonical, review_class=PUBLIC_REVIEW_ALLOWED, protected=False)


def normalize_privacy_boundary(value: object) -> NormalizedPrivacyBoundary:
    raw = _string(value, "privacy_boundary")
    portions = tuple(
        part.strip().upper().replace("-", "_")
        for part in raw.split("/")
        if part.strip()
    )
    public_safe: list[str] = []
    private: list[str] = []
    unknown: list[str] = []
    for portion in portions:
        if portion in _PUBLIC_SAFE_PRIVACY_BOUNDARIES:
            public_safe.append(portion)
        elif any(marker in portion for marker in _PRIVATE_MARKERS):
            private.append(_redacted_private_portion(portion))
        else:
            unknown.append(portion)
    if private:
        return NormalizedPrivacyBoundary(
            privacy_class=PRIVATE_PROTECTED,
            protected=True,
            public_safe_portions=tuple(public_safe),
            private_portions=tuple(private),
        )
    if public_safe and not unknown:
        return NormalizedPrivacyBoundary(
            privacy_class=PUBLIC_SAFE,
            protected=False,
            public_safe_portions=tuple(public_safe),
            private_portions=(),
        )
    return NormalizedPrivacyBoundary(
        privacy_class=UNKNOWN_BOUNDARY,
        protected=True,
        public_safe_portions=tuple(public_safe),
        private_portions=(),
    )


def evaluate_task_quality(value: Mapping[str, Any]) -> TaskQualityGateDecision:
    task = Phase1Task.from_mapping(value)
    surfaces = protected_surfaces(task.allowed_files)
    evidence = evaluate_phase1_evidence(task.payload)
    reason_codes: list[str] = []
    if task.risk.protected:
        reason_codes.append("PROTECTED_RISK")
    if task.privacy_boundary.protected:
        reason_codes.append("PROTECTED_PRIVACY_BOUNDARY")
    if surfaces:
        reason_codes.append("PROTECTED_SURFACE")
    if not evidence.accepted:
        reason_codes.append("CALLER_PROOF_REJECTED")

    status = (
        "protected_review_required"
        if reason_codes
        else "public_review_allowed"
    )
    return TaskQualityGateDecision(
        status=status,
        task=task,
        protected_surfaces=surfaces,
        evidence=evidence,
        reason_codes=tuple(reason_codes),
    )


def protected_surfaces(paths: Sequence[str]) -> tuple[str, ...]:
    found: list[str] = []
    for path in paths:
        if path in _PROTECTED_SURFACE_EXACT or any(
            fnmatch.fnmatchcase(path, pattern) for pattern in _PROTECTED_SURFACE_GLOBS
        ):
            found.append(path)
    return tuple(sorted(set(found)))


def _coalesced_alias(value: Mapping[str, Any], primary: str, alias: str) -> object:
    has_primary = primary in value
    has_alias = alias in value
    if has_primary and has_alias:
        if _string(value[primary], primary).strip() != _string(value[alias], alias).strip():
            raise TaskQualityGateError(
                "ALIAS_DISAGREEMENT",
                f"{primary} and {alias} disagree",
            )
    if has_primary:
        return value[primary]
    if has_alias:
        return value[alias]
    raise TaskQualityGateError("MISSING_FIELD", f"missing {primary}")


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not _SAFE_TOKEN_RE.fullmatch(value):
        raise TaskQualityGateError("INVALID_STRING", f"{field} must be a bounded string")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str) or not value:
        raise TaskQualityGateError("INVALID_SEQUENCE", f"{field} must be a non-empty sequence")
    items = tuple(_string(item, field) for item in value)
    if len(set(items)) != len(items):
        raise TaskQualityGateError("DUPLICATE_SEQUENCE_ITEM", f"{field} must be unique")
    return items


def _optional_string_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    return _string_tuple(value, field)


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TaskQualityGateError("INVALID_MAPPING", f"{field} must be an object")
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: object) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _redacted_private_portion(value: str) -> str:
    markers = tuple(marker for marker in _PRIVATE_MARKERS if marker in value)
    if not markers:
        return "PRIVATE_PORTION"
    return "PRIVATE_PORTION:" + "+".join(markers)
