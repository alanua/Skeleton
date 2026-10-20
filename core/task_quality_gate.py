from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
import re
from types import MappingProxyType
from typing import Any, Final


TASK_CLAIM_SCHEMA: Final = "skeleton.phase1_task_claim.v1"

SUPPORTED_CLAIM_FIELDS: Final = frozenset(
    {
        "schema",
        "project",
        "repo",
        "base",
        "base_sha",
        "branch",
        "task_kind",
        "payload",
        "requested_capabilities",
        "allowed_files",
        "forbidden_actions",
        "validation",
        "required_tests",
        "expected_output",
        "privacy",
        "privacy_boundary",
        "idempotency",
        "idempotency_key",
        "risk",
    }
)

CALLER_PROOF_FIELDS: Final = frozenset(
    {
        "ARCHITECTURE_GREEN",
        "PRODUCTION_CONTRACT_GREEN",
        "ObservedDiffImpact",
        "observed_diff_impact",
        "touched_files",
        "RUNTIME_PROVEN",
        "runtime_proven",
        "architecture_required",
        "production_ready",
        "runtime_proof",
    }
)

RISK_ALIASES: Final = MappingProxyType(
    {
        "green": "green",
        "low": "green",
        "public-safe": "green",
        "public_safe": "green",
        "yellow": "yellow",
        "medium": "yellow",
        "review": "yellow",
        "review-relevant": "yellow",
        "review_relevant": "yellow",
        "red": "red",
        "high": "red",
        "critical": "critical",
        "protected": "critical",
    }
)

_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,127}$")
_REPO_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+ -]{0,255}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9._@+-][A-Za-z0-9._@+/*-]{0,511}$")
_MAX_LIST_ITEMS = 128
_MAX_TEXT_LENGTH = 1024
_MAX_PAYLOAD_DEPTH = 16
_MAX_PAYLOAD_ITEMS = 256
_MAX_PAYLOAD_STRING_LENGTH = 4096
_MAX_PAYLOAD_BYTES = 65536


class TaskQualityGateError(ValueError):
    """Raised when a Phase 1 task claim is malformed or out of policy."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RepositoryPath:
    value: str
    kind: str


@dataclass(frozen=True)
class Phase1TaskClaim:
    schema: str | None
    project: str | None
    repo: str | None
    base: str | None
    base_sha: str | None
    branch: str | None
    task_kind: str | None
    payload: Mapping[str, Any]
    requested_capabilities: tuple[str, ...]
    allowed_files: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    validation: tuple[str, ...]
    required_tests: tuple[str, ...]
    expected_output: tuple[str, ...]
    privacy: str | None
    privacy_boundary: str | None
    idempotency: str | None
    idempotency_key: str | None
    risk: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Phase1TaskClaim":
        return validate_task_claim(value)

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field in (
            "schema",
            "project",
            "repo",
            "base",
            "base_sha",
            "branch",
            "task_kind",
            "privacy",
            "privacy_boundary",
            "idempotency",
            "idempotency_key",
        ):
            item = getattr(self, field)
            if item is not None:
                result[field] = item
        result["payload"] = _thaw_json(self.payload)
        result["requested_capabilities"] = list(self.requested_capabilities)
        result["allowed_files"] = list(self.allowed_files)
        result["forbidden_actions"] = list(self.forbidden_actions)
        result["validation"] = list(self.validation)
        result["required_tests"] = list(self.required_tests)
        result["expected_output"] = list(self.expected_output)
        result["risk"] = self.risk
        return result


def validate_task_claim(value: Mapping[str, Any]) -> Phase1TaskClaim:
    if not isinstance(value, Mapping):
        raise TaskQualityGateError("INVALID_TASK_CLAIM", "task claim must be an object")
    if any(not isinstance(key, str) for key in value):
        raise TaskQualityGateError("INVALID_TASK_FIELD", "task claim keys must be strings")
    proof_fields = sorted(set(value).intersection(CALLER_PROOF_FIELDS))
    if proof_fields:
        raise TaskQualityGateError(
            "CALLER_PROOF_REJECTED",
            f"caller-supplied proof field is not accepted: {proof_fields[0]}",
        )
    unknown = sorted(set(value) - SUPPORTED_CLAIM_FIELDS)
    if unknown:
        raise TaskQualityGateError(
            "UNKNOWN_TASK_FIELD",
            f"unsupported task claim field: {unknown[0]}",
        )

    return Phase1TaskClaim(
        schema=_optional_schema(value.get("schema")),
        project=_optional_project(value.get("project")),
        repo=_optional_repo(value.get("repo")),
        base=_optional_ref(value.get("base"), "base"),
        base_sha=_optional_sha(value.get("base_sha")),
        branch=_optional_ref(value.get("branch"), "branch"),
        task_kind=_optional_token(value.get("task_kind"), "task_kind"),
        payload=_payload(value.get("payload", {})),
        requested_capabilities=_text_list(
            value.get("requested_capabilities", ()),
            field="requested_capabilities",
            allow_repo_text=False,
        ),
        allowed_files=_allowed_files(value.get("allowed_files")),
        forbidden_actions=_text_list(
            value.get("forbidden_actions", ()),
            field="forbidden_actions",
            allow_repo_text=True,
        ),
        validation=_text_list(
            value.get("validation", ()),
            field="validation",
            allow_repo_text=True,
        ),
        required_tests=_text_list(
            value.get("required_tests", ()),
            field="required_tests",
            allow_repo_text=True,
        ),
        expected_output=_text_list(
            value.get("expected_output", ()),
            field="expected_output",
            allow_repo_text=True,
        ),
        privacy=_optional_text(value.get("privacy"), "privacy"),
        privacy_boundary=_optional_text(value.get("privacy_boundary"), "privacy_boundary"),
        idempotency=_optional_token(value.get("idempotency"), "idempotency"),
        idempotency_key=_optional_token(value.get("idempotency_key"), "idempotency_key"),
        risk=_risk(value.get("risk", "green")),
    )


def validate_repository_path(value: object) -> RepositoryPath:
    if not isinstance(value, str) or not value:
        raise TaskQualityGateError(
            "INVALID_REPOSITORY_PATH",
            "repository path must be a non-empty string",
        )
    _reject_control(value, "repository path")
    if (
        value.startswith("/")
        or "\\" in value
        or "//" in value
        or value in {".", "..", "**"}
        or not _PATH_RE.fullmatch(value)
    ):
        raise TaskQualityGateError(
            "INVALID_REPOSITORY_PATH",
            "repository path must be bounded and repository-relative",
        )
    if "*" in value:
        return _directory_scope(value)
    return _exact_path(value)


def is_repository_path(value: object) -> bool:
    try:
        validate_repository_path(value)
    except TaskQualityGateError:
        return False
    return True


def _directory_scope(value: str) -> RepositoryPath:
    if not value.endswith("/**"):
        raise TaskQualityGateError(
            "INVALID_REPOSITORY_SCOPE",
            "only bounded directory/** scopes are accepted",
        )
    prefix = value[:-3]
    if not prefix or prefix.endswith("/") or "*" in prefix:
        raise TaskQualityGateError(
            "INVALID_REPOSITORY_SCOPE",
            "directory scope prefix must be non-empty and exact",
        )
    _validate_segments(prefix, "INVALID_REPOSITORY_SCOPE")
    return RepositoryPath(value=value, kind="directory_scope")


def _exact_path(value: str) -> RepositoryPath:
    if value.endswith("/"):
        raise TaskQualityGateError(
            "INVALID_REPOSITORY_PATH",
            "exact repository path must not end with slash",
        )
    _validate_segments(value, "INVALID_REPOSITORY_PATH")
    return RepositoryPath(value=value, kind="exact_path")


def _validate_segments(value: str, reason_code: str) -> None:
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise TaskQualityGateError(
            reason_code,
            "repository path must not contain empty, current, or parent segments",
        )


def _allowed_files(value: object) -> tuple[str, ...]:
    items = _sequence(value, "allowed_files", require_non_empty=True)
    normalized = [validate_repository_path(item).value for item in items]
    _reject_duplicates(normalized, "allowed_files")
    return tuple(normalized)


def _text_list(value: object, *, field: str, allow_repo_text: bool) -> tuple[str, ...]:
    items = _sequence(value, field, require_non_empty=False)
    if len(items) > _MAX_LIST_ITEMS:
        raise TaskQualityGateError(
            f"TOO_MANY_{field.upper()}",
            f"{field} contains too many entries",
        )
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item or len(item) > _MAX_TEXT_LENGTH:
            raise TaskQualityGateError(
                f"INVALID_{field.upper()}",
                f"{field} entries must be bounded non-empty strings",
            )
        _reject_control(item, field)
        if not allow_repo_text and not _TOKEN_RE.fullmatch(item):
            raise TaskQualityGateError(
                f"INVALID_{field.upper()}",
                f"{field} entries must be bounded tokens",
            )
        normalized.append(item)
    _reject_duplicates(normalized, field)
    return tuple(normalized)


def _optional_schema(value: object) -> str | None:
    if value is None:
        return None
    if value != TASK_CLAIM_SCHEMA:
        raise TaskQualityGateError(
            "INVALID_TASK_SCHEMA",
            f"schema must equal {TASK_CLAIM_SCHEMA}",
        )
    return TASK_CLAIM_SCHEMA


def _optional_project(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _PROJECT_RE.fullmatch(value):
        raise TaskQualityGateError("INVALID_PROJECT", "project is malformed")
    _reject_control(value, "project")
    return value


def _optional_repo(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _REPO_RE.fullmatch(value):
        raise TaskQualityGateError("INVALID_REPOSITORY", "repo is malformed")
    return value


def _optional_ref(value: object, field: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not _REF_RE.fullmatch(value)
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
        or any(segment in {"", ".", ".."} for segment in value.split("/"))
    ):
        raise TaskQualityGateError(f"INVALID_{field.upper()}", f"{field} is malformed")
    return value


def _optional_sha(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise TaskQualityGateError(
            "INVALID_BASE_SHA",
            "base_sha must be a full 40-character commit SHA",
        )
    return value.lower()


def _optional_token(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise TaskQualityGateError(
            f"INVALID_{field.upper()}",
            f"{field} must be a bounded token",
        )
    _reject_control(value, field)
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT_LENGTH:
        raise TaskQualityGateError(
            f"INVALID_{field.upper()}",
            f"{field} must be bounded text",
        )
    _reject_control(value, field)
    return value


def _risk(value: object) -> str:
    if not isinstance(value, str):
        raise TaskQualityGateError("INVALID_RISK", "risk must be a string")
    key = value.strip().lower()
    if key not in RISK_ALIASES:
        raise TaskQualityGateError("INVALID_RISK", "risk is not recognized")
    return RISK_ALIASES[key]


def _payload(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TaskQualityGateError("INVALID_PAYLOAD", "payload must be an object")
    frozen = _freeze_json(value, path="payload", depth=0)
    assert isinstance(frozen, Mapping)
    serialized = json.dumps(
        _thaw_json(frozen),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(serialized.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise TaskQualityGateError("PAYLOAD_TOO_LARGE", "payload exceeds size bound")
    return frozen


def _freeze_json(value: object, *, path: str, depth: int) -> Any:
    if depth > _MAX_PAYLOAD_DEPTH:
        raise TaskQualityGateError("PAYLOAD_TOO_DEEP", f"{path} is too deep")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TaskQualityGateError("INVALID_PAYLOAD", f"{path} is non-finite")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_PAYLOAD_STRING_LENGTH:
            raise TaskQualityGateError("PAYLOAD_STRING_TOO_LONG", f"{path} is too long")
        _reject_control(value, path)
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_PAYLOAD_ITEMS:
            raise TaskQualityGateError("PAYLOAD_TOO_LARGE", f"{path} has too many keys")
        keys = list(value)
        if any(not isinstance(key, str) or not _TOKEN_RE.fullmatch(key) for key in keys):
            raise TaskQualityGateError("INVALID_PAYLOAD_KEY", f"{path} has invalid keys")
        return MappingProxyType(
            {
                key: _freeze_json(value[key], path=f"{path}.{key}", depth=depth + 1)
                for key in sorted(keys)
            }
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > _MAX_PAYLOAD_ITEMS:
            raise TaskQualityGateError("PAYLOAD_TOO_LARGE", f"{path} has too many items")
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        )
    raise TaskQualityGateError("INVALID_PAYLOAD", f"{path} contains non-JSON data")


def _thaw_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _sequence(value: object, field: str, *, require_non_empty: bool) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TaskQualityGateError(f"INVALID_{field.upper()}", f"{field} must be an array")
    if require_non_empty and not value:
        raise TaskQualityGateError(f"EMPTY_{field.upper()}", f"{field} must not be empty")
    return value


def _reject_control(value: str, field: str) -> None:
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise TaskQualityGateError(
            f"INVALID_{field.upper().replace(' ', '_')}",
            f"{field} contains control characters",
        )


def _reject_duplicates(values: Sequence[object], field: str) -> None:
    if len(set(values)) != len(values):
        raise TaskQualityGateError(
            f"DUPLICATE_{field.upper()}",
            f"{field} contains duplicate entries",
        )
