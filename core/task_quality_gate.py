from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any, Final

from core.quality_evidence import (
    Phase1EvidenceClassification,
    QualityEvidenceError,
    assert_no_phase1_proof_claims,
    canonical_json,
    classify_phase1_evidence,
    freeze_json,
    public_evidence_mapping,
    thaw_json,
)


TASKSPEC_SCHEMA: Final = "skeleton.runner_task.v1"
TASK_KINDS: Final = frozenset(
    {
        "code_generation",
        "code_edit",
        "repository_maintenance",
        "private_memory",
        "diagnostic",
        "loop_control",
        "publish",
    }
)
REQUESTED_CAPABILITIES: Final = frozenset(
    {
        "repository_read",
        "repository_write",
        "repository_write_allowlisted",
        "test_execution",
        "subprocess_isolated",
        "memory_gateway_read",
        "memory_gateway_write",
        "diagnostic_read",
        "repository_maintenance",
        "loop_control",
        "publish_pull_request",
    }
)
PROTECTED_SURFACE: Final = (
    "BOOT_MANIFEST.yaml",
    "PROJECT_TREE.yaml",
    "OPERATOR_RULES.yaml",
    "CAPABILITY_REGISTRY.yaml",
    "INVARIANTS.yaml",
    ".github/workflows/**",
    "scripts/runner_poll_github_tasks.py",
    "core/gate_engine.py",
    "core/action_gate.py",
    "core/architecture_invariants.py",
    "core/runner_**",
    "secrets/**",
    "deploy/**",
    "server/**",
    "finance/**",
    "legal/**",
    "governance/**",
    "Runner_core/**",
    "adapter_boundaries/**",
)

_ALIAS_GROUPS: Final = {
    "repo": ("repo", "repository"),
    "base": ("base", "base_ref", "target_ref"),
    "base_sha": ("base_sha", "expected_base_sha"),
    "branch": ("branch", "head_ref"),
    "head_sha": ("head_sha", "expected_head_sha", "expected_sha"),
    "requested_capabilities": ("requested_capabilities", "capabilities"),
    "allowed_files": ("allowed_files", "allowed_paths", "allowed_scopes", "scopes"),
    "validation": ("validation", "validation_commands"),
    "privacy_boundary": ("privacy_boundary", "privacy"),
    "risk": ("risk", "risk_level"),
}
_KNOWN_FIELDS: Final = frozenset(
    {
        "schema",
        "task_kind",
        "payload",
        "forbidden_actions",
        "expected_output",
        "idempotency_key",
        "idempotency",
        "protected_intent",
        "architecture_required",
        "production_contract_required",
    }
).union(*(frozenset(values) for values in _ALIAS_GROUPS.values()))

_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,255}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9._@+-][A-Za-z0-9._/@+*-]{0,511}$")


class TaskQualityGateError(ValueError):
    """Raised when a Phase 1 TaskSpec cannot be losslessly normalized."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class DeclaredScope:
    path: str
    mode: str = "DECLARED_ONLY"
    protected: bool = False

    def to_mapping(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mode": self.mode,
            "protected": self.protected,
        }


@dataclass(frozen=True)
class NormalizedTaskSpec:
    schema: str
    repo: str
    base: str
    base_sha: str | None
    branch: str
    head_sha: str | None
    task_kind: str
    payload: Mapping[str, Any]
    requested_capabilities: tuple[str, ...]
    allowed_scopes: tuple[DeclaredScope, ...]
    forbidden_actions: tuple[str, ...]
    validation: tuple[str, ...]
    expected_output: tuple[str, ...]
    privacy_boundary: str
    idempotency_key: str
    risk: str
    protected_intent: bool
    architecture_required: bool
    production_contract_required: bool
    classification: Phase1EvidenceClassification

    @property
    def allowed_files(self) -> tuple[str, ...]:
        return tuple(scope.path for scope in self.allowed_scopes)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "repo": self.repo,
            "base": self.base,
            "base_sha": self.base_sha,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "task_kind": self.task_kind,
            "payload": thaw_json(self.payload),
            "requested_capabilities": list(self.requested_capabilities),
            "allowed_files": list(self.allowed_files),
            "allowed_scopes": [scope.to_mapping() for scope in self.allowed_scopes],
            "forbidden_actions": list(self.forbidden_actions),
            "validation": list(self.validation),
            "expected_output": list(self.expected_output),
            "privacy_boundary": self.privacy_boundary,
            "idempotency_key": self.idempotency_key,
            "risk": self.risk,
            "protected_intent": self.protected_intent,
            "architecture_required": self.architecture_required,
            "production_contract_required": self.production_contract_required,
            "classification": public_evidence_mapping(self.classification),
        }

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "repo": self.repo,
            "base": self.base,
            "base_sha_present": self.base_sha is not None,
            "branch": self.branch,
            "head_sha_present": self.head_sha is not None,
            "task_kind": self.task_kind,
            "payload_present": True,
            "requested_capabilities": list(self.requested_capabilities),
            "allowed_scopes": [scope.to_mapping() for scope in self.allowed_scopes],
            "forbidden_actions": list(self.forbidden_actions),
            "validation": list(self.validation),
            "expected_output": list(self.expected_output),
            "privacy_boundary": self.privacy_boundary,
            "idempotency_key": self.idempotency_key,
            "risk": self.risk,
            "protected_intent": self.protected_intent,
            "architecture_required": self.architecture_required,
            "production_contract_required": self.production_contract_required,
            "classification": public_evidence_mapping(self.classification),
        }


def normalize_task_spec(value: Mapping[str, Any]) -> NormalizedTaskSpec:
    if not isinstance(value, Mapping):
        raise TaskQualityGateError("INVALID_TASKSPEC", "TaskSpec must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise TaskQualityGateError("INVALID_TASKSPEC_FIELD", "TaskSpec keys must be strings")
    try:
        assert_no_phase1_proof_claims(value, path="taskspec")
    except QualityEvidenceError as exc:
        raise TaskQualityGateError(exc.reason_code, str(exc)) from exc

    unknown = sorted(set(value) - _KNOWN_FIELDS)
    if unknown:
        raise TaskQualityGateError(
            "UNKNOWN_TASKSPEC_FIELD",
            f"unknown TaskSpec field: {unknown[0]}",
        )

    schema = _optional_exact(value.get("schema", TASKSPEC_SCHEMA), "schema", TASKSPEC_SCHEMA)
    repo = _repository(_required_alias(value, "repo"))
    base = _ref(_required_alias(value, "base"), "base")
    base_sha = _optional_sha(_optional_alias(value, "base_sha"), "base_sha")
    branch = _ref(_required_alias(value, "branch"), "branch")
    head_sha = _optional_sha(_optional_alias(value, "head_sha"), "head_sha")
    task_kind = _enum(_required_field(value, "task_kind"), "task_kind", TASK_KINDS)
    payload = freeze_json(_required_field(value, "payload"), path="payload")
    requested_capabilities = _unique_sorted_strings(
        _required_alias(value, "requested_capabilities"),
        field="requested_capabilities",
        allowed=REQUESTED_CAPABILITIES,
    )
    allowed_scopes = _declared_scopes(_required_alias(value, "allowed_files"))
    forbidden_actions = _unique_sorted_text(
        _required_field(value, "forbidden_actions"),
        field="forbidden_actions",
    )
    validation = _unique_ordered_text(
        _required_alias(value, "validation"),
        field="validation",
    )
    expected_output = _unique_ordered_text(
        _required_field(value, "expected_output"),
        field="expected_output",
    )
    privacy_boundary = _text(_required_alias(value, "privacy_boundary"), "privacy_boundary").upper()
    idempotency_key = _idempotency_key(
        _one_of_idempotency(value),
    )
    risk = _text(value.get("risk", "LOW"), "risk").upper()
    protected_intent = _bool(value.get("protected_intent", False), "protected_intent")
    architecture_required = _bool(
        value.get("architecture_required", False),
        "architecture_required",
    )
    production_contract_required = _bool(
        value.get("production_contract_required", False),
        "production_contract_required",
    )

    try:
        classification = classify_phase1_evidence(
            architecture_required=architecture_required,
            production_contract_required=production_contract_required,
            protected_scope_declared=any(scope.protected for scope in allowed_scopes),
            protected_intent=protected_intent,
            privacy_boundary=privacy_boundary,
            risk=risk,
        )
    except QualityEvidenceError as exc:
        raise TaskQualityGateError(exc.reason_code, str(exc)) from exc

    return NormalizedTaskSpec(
        schema=schema,
        repo=repo,
        base=base,
        base_sha=base_sha,
        branch=branch,
        head_sha=head_sha,
        task_kind=task_kind,
        payload=payload,
        requested_capabilities=requested_capabilities,
        allowed_scopes=allowed_scopes,
        forbidden_actions=forbidden_actions,
        validation=validation,
        expected_output=expected_output,
        privacy_boundary=privacy_boundary,
        idempotency_key=idempotency_key,
        risk=risk,
        protected_intent=protected_intent,
        architecture_required=architecture_required,
        production_contract_required=production_contract_required,
        classification=classification,
    )


def normalize_task_spec_public(value: Mapping[str, Any]) -> dict[str, Any]:
    return normalize_task_spec(value).to_public_mapping()


def is_protected_declared_scope(path: str) -> bool:
    normalized = _path(path)
    for protected in PROTECTED_SURFACE:
        if _protected_pattern_matches(protected, normalized):
            return True
    return False


def validate_head_bound_metadata(
    normalized: NormalizedTaskSpec,
    *,
    current_head_sha: str | None,
) -> bool:
    if normalized.head_sha is None:
        return True
    current = _optional_sha(current_head_sha, "current_head_sha")
    return current == normalized.head_sha


def _required_field(value: Mapping[str, Any], field: str) -> Any:
    if field not in value:
        raise TaskQualityGateError("MISSING_TASKSPEC_FIELD", f"missing TaskSpec field: {field}")
    return value[field]


def _optional_alias(value: Mapping[str, Any], canonical: str) -> Any | None:
    aliases = _ALIAS_GROUPS[canonical]
    present = [alias for alias in aliases if alias in value]
    if not present:
        return None
    first = value[present[0]]
    try:
        first_json = canonical_json(first)
    except QualityEvidenceError as exc:
        raise TaskQualityGateError(exc.reason_code, str(exc)) from exc
    for alias in present[1:]:
        try:
            alias_json = canonical_json(value[alias])
        except QualityEvidenceError as exc:
            raise TaskQualityGateError(exc.reason_code, str(exc)) from exc
        if alias_json != first_json:
            raise TaskQualityGateError(
                "AMBIGUOUS_TASKSPEC_ALIAS",
                f"{canonical} aliases disagree",
            )
    return first


def _required_alias(value: Mapping[str, Any], canonical: str) -> Any:
    selected = _optional_alias(value, canonical)
    if selected is None:
        raise TaskQualityGateError(
            "MISSING_TASKSPEC_FIELD",
            f"missing TaskSpec field: {canonical}",
        )
    return selected


def _one_of_idempotency(value: Mapping[str, Any]) -> Any:
    present = [field for field in ("idempotency_key", "idempotency") if field in value]
    if not present:
        raise TaskQualityGateError(
            "MISSING_TASKSPEC_FIELD",
            "missing TaskSpec field: idempotency_key",
        )
    first = value[present[0]]
    try:
        first_json = canonical_json(first)
    except QualityEvidenceError as exc:
        raise TaskQualityGateError(exc.reason_code, str(exc)) from exc
    for field in present[1:]:
        try:
            field_json = canonical_json(value[field])
        except QualityEvidenceError as exc:
            raise TaskQualityGateError(exc.reason_code, str(exc)) from exc
        if field_json != first_json:
            raise TaskQualityGateError(
                "AMBIGUOUS_TASKSPEC_ALIAS",
                "idempotency aliases disagree",
            )
    return first


def _optional_exact(value: object, field: str, expected: str) -> str:
    if value != expected:
        raise TaskQualityGateError(
            f"INVALID_{field.upper()}",
            f"{field} must equal {expected}",
        )
    return expected


def _repository(value: object) -> str:
    text = _text(value, "repo")
    if not _REPOSITORY_RE.fullmatch(text):
        raise TaskQualityGateError("INVALID_REPOSITORY", "repo is malformed")
    return text


def _ref(value: object, field: str) -> str:
    text = _text(value, field)
    if (
        not _REF_RE.fullmatch(text)
        or text.endswith(("/", ".", ".lock"))
        or "\\" in text
        or ".." in text
        or "//" in text
        or any(segment in {"", ".", ".."} for segment in text.split("/"))
    ):
        raise TaskQualityGateError(f"INVALID_{field.upper()}", f"{field} is malformed")
    return text


def _optional_sha(value: object, field: str) -> str | None:
    if value is None:
        return None
    text = _text(value, field)
    if not _SHA_RE.fullmatch(text):
        raise TaskQualityGateError(f"INVALID_{field.upper()}", f"{field} is malformed")
    return text.lower()


def _enum(value: object, field: str, allowed: frozenset[str]) -> str:
    text = _text(value, field)
    if text not in allowed:
        raise TaskQualityGateError(
            f"INVALID_{field.upper()}",
            f"{field} is not allowlisted",
        )
    return text


def _idempotency_key(value: object) -> str:
    text = _text(value, "idempotency_key")
    if not _IDEMPOTENCY_RE.fullmatch(text):
        raise TaskQualityGateError(
            "INVALID_IDEMPOTENCY_KEY",
            "idempotency_key must be bounded policy metadata",
        )
    return text


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or _has_control_char(value):
        raise TaskQualityGateError(f"INVALID_{field.upper()}", f"{field} must be text")
    return value.strip()


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise TaskQualityGateError(f"INVALID_{field.upper()}", f"{field} must be boolean")
    return value


def _unique_sorted_strings(
    value: object,
    *,
    field: str,
    allowed: frozenset[str],
) -> tuple[str, ...]:
    items = _string_sequence(value, field)
    normalized = tuple(sorted(_text(item, field) for item in items))
    if len(set(normalized)) != len(normalized):
        raise TaskQualityGateError(f"DUPLICATE_{field.upper()}", f"{field} has duplicates")
    invalid = [item for item in normalized if item not in allowed]
    if invalid:
        raise TaskQualityGateError(
            f"INVALID_{field.upper()}",
            f"{field} contains an unallowlisted value: {invalid[0]}",
        )
    return normalized


def _unique_sorted_text(value: object, *, field: str) -> tuple[str, ...]:
    items = tuple(sorted(_bounded_text_sequence(value, field)))
    if len(set(items)) != len(items):
        raise TaskQualityGateError(f"DUPLICATE_{field.upper()}", f"{field} has duplicates")
    return items


def _unique_ordered_text(value: object, *, field: str) -> tuple[str, ...]:
    items = _bounded_text_sequence(value, field)
    if len(set(items)) != len(items):
        raise TaskQualityGateError(f"DUPLICATE_{field.upper()}", f"{field} has duplicates")
    return items


def _bounded_text_sequence(value: object, field: str) -> tuple[str, ...]:
    return tuple(_bounded_text(item, field) for item in _string_sequence(value, field))


def _bounded_text(value: object, field: str) -> str:
    text = _text(value, field)
    if len(text) > 1024:
        raise TaskQualityGateError(f"INVALID_{field.upper()}", f"{field} is too long")
    return text


def _string_sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TaskQualityGateError(f"INVALID_{field.upper()}", f"{field} must be a list")
    if not value:
        raise TaskQualityGateError(f"EMPTY_{field.upper()}", f"{field} must not be empty")
    if len(value) > 128:
        raise TaskQualityGateError(f"TOO_MANY_{field.upper()}", f"{field} has too many items")
    return value


def _declared_scopes(value: object) -> tuple[DeclaredScope, ...]:
    paths = tuple(sorted(_path(item) for item in _string_sequence(value, "allowed_files")))
    if len(set(paths)) != len(paths):
        raise TaskQualityGateError("DUPLICATE_ALLOWED_FILES", "allowed_files has duplicates")
    return tuple(
        DeclaredScope(path=path, protected=is_protected_declared_scope(path))
        for path in paths
    )


def _path(value: object) -> str:
    text = _text(value, "allowed_files")
    if (
        not _PATH_RE.fullmatch(text)
        or text.startswith("/")
        or text.endswith("/")
        or "\\" in text
        or "//" in text
        or any(segment in {"", ".", ".."} for segment in text.split("/"))
    ):
        raise TaskQualityGateError(
            "INVALID_ALLOWED_FILE",
            "allowed_files must contain repository-relative paths",
        )
    star_count = text.count("*")
    if star_count:
        if not text.endswith("/**") or star_count != 2:
            raise TaskQualityGateError(
                "INVALID_ALLOWED_FILE",
                "allowed_files may only use bounded /** globs",
            )
    return text


def _protected_pattern_matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/**"):
        return path == pattern[:-3] or path.startswith(pattern[:-2])
    if pattern.endswith("_**"):
        return path.startswith(pattern[:-2])
    return path == pattern


def _has_control_char(value: str) -> bool:
    return any(ord(character) < 32 for character in value)
