from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Mapping

from core.canonical_memory import (
    CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
    CANONICAL_OPERATOR_PREFERENCES_SCOPE,
    FAST_AUTONOMOUS_EXECUTION_KEY,
    OPERATOR_WORKING_STYLE_RECORD_TYPE,
)


CANONICAL_MEMORY_MANIFEST_SCHEMA = "skeleton.canonical_memory_manifest.v1"
SUPPORTED_CANONICAL_NAMESPACES = frozenset({CANONICAL_OPERATOR_PREFERENCES_NAMESPACE})
SUPPORTED_PRIVACY_CLASSIFICATIONS = frozenset({"public_safe_operator_preference"})
SUPPORTED_AUTHORITY = frozenset({"candidate_manifest_only"})
SUPPORTED_PROVENANCE_KINDS = frozenset({"approved_github_issue_comment"})
SUPPORTED_SUPERSESSION_STATUS = frozenset({"initial", "supersedes"})
FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "raw_chat",
        "raw_transcript",
        "transcript",
        "conversation",
        "message_log",
        "raw_content",
        "content",
        "secret",
        "token",
        "password",
        "credential",
        "local_path",
        "raw_path",
        "path",
        "sqlite_path",
        "storage_api",
        "direct_sqlite_write",
        "write_intent",
        "runtime_activation",
    }
)
FORBIDDEN_VALUE_FRAGMENTS = (
    "://",
    "/home/",
    "/tmp/",
    "\\users\\",
    ".sqlite",
    ".db",
    "secret",
    "token",
    "password",
    "credential",
    "customer",
    "private-value",
    "raw transcript",
    "raw chat",
    "direct sqlite",
)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)


def canonical_manifest_integrity_hash(manifest: Mapping[str, Any]) -> str:
    """Return the deterministic hash over manifest content excluding its hash field."""
    candidate = deepcopy(dict(manifest))
    candidate.pop("integrity_hash", None)
    serialized = json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(serialized.encode("utf-8")).hexdigest()


def validate_canonical_memory_manifest(data: Mapping[str, Any]) -> ValidationResult:
    errors: list[ValidationIssue] = []
    if not isinstance(data, Mapping):
        return ValidationResult(
            ok=False,
            errors=[_issue("$", "invalid_manifest", "manifest must be an object")],
        )

    _require(data, "schema", "$.schema", errors)
    _require(data, "namespace", "$.namespace", errors)
    _require(data, "scope", "$.scope", errors)
    _require(data, "key", "$.key", errors)
    _require(data, "record_type", "$.record_type", errors)
    _require(data, "version", "$.version", errors)
    _require(data, "authority", "$.authority", errors)
    _require(data, "privacy_classification", "$.privacy_classification", errors)
    _require(data, "provenance", "$.provenance", errors)
    _require(data, "supersession", "$.supersession", errors)
    _require(data, "record", "$.record", errors)
    _require(data, "integrity_hash", "$.integrity_hash", errors)

    if data.get("schema") != CANONICAL_MEMORY_MANIFEST_SCHEMA:
        errors.append(_issue("$.schema", "unsupported_schema", "manifest schema is unsupported"))
    if data.get("namespace") not in SUPPORTED_CANONICAL_NAMESPACES:
        errors.append(_issue("$.namespace", "unsupported_namespace", "canonical namespace is unsupported"))
    if data.get("scope") != CANONICAL_OPERATOR_PREFERENCES_SCOPE:
        errors.append(_issue("$.scope", "unsupported_scope", "canonical scope is unsupported"))
    if data.get("key") != FAST_AUTONOMOUS_EXECUTION_KEY:
        errors.append(_issue("$.key", "unsupported_key", "canonical key is unsupported"))
    if data.get("record_type") != OPERATOR_WORKING_STYLE_RECORD_TYPE:
        errors.append(_issue("$.record_type", "unsupported_record_type", "record type is unsupported"))
    if data.get("authority") not in SUPPORTED_AUTHORITY:
        errors.append(_issue("$.authority", "direct_sqlite_write_intent", "authority must remain candidate-only"))
    if data.get("privacy_classification") not in SUPPORTED_PRIVACY_CLASSIFICATIONS:
        errors.append(_issue("$.privacy_classification", "unsupported_privacy_classification", "privacy class is unsupported"))
    if not isinstance(data.get("version"), int) or data.get("version", 0) < 1:
        errors.append(_issue("$.version", "invalid_version", "version must be a positive integer"))

    provenance = data.get("provenance")
    if isinstance(provenance, Mapping):
        _validate_provenance(provenance, errors)
    elif "provenance" in data:
        errors.append(_issue("$.provenance", "invalid_provenance", "provenance must be an object"))

    supersession = data.get("supersession")
    if isinstance(supersession, Mapping):
        if supersession.get("status") not in SUPPORTED_SUPERSESSION_STATUS:
            errors.append(_issue("$.supersession.status", "unsupported_supersession", "supersession status is unsupported"))
        supersedes = supersession.get("supersedes")
        if supersedes is not None and not isinstance(supersedes, list):
            errors.append(_issue("$.supersession.supersedes", "invalid_supersedes", "supersedes must be an array"))
    elif "supersession" in data:
        errors.append(_issue("$.supersession", "invalid_supersession", "supersession must be an object"))

    integrity_hash = data.get("integrity_hash")
    if not isinstance(integrity_hash, str) or not _SHA256_RE.fullmatch(integrity_hash):
        errors.append(_issue("$.integrity_hash", "invalid_integrity_hash", "integrity hash must be sha256 hex"))
    elif integrity_hash != canonical_manifest_integrity_hash(data):
        errors.append(_issue("$.integrity_hash", "integrity_hash_mismatch", "integrity hash does not match manifest"))

    _reject_private_or_raw_values(data, "$", errors)
    return ValidationResult(ok=not errors, errors=errors)


def prepare_canonical_memory_manifest(data: Mapping[str, Any]) -> dict[str, object]:
    result = validate_canonical_memory_manifest(data)
    if not result.ok:
        return {
            "status": "REJECTED",
            "errors": [_issue_to_dict(issue) for issue in result.errors],
            "warnings": [_issue_to_dict(issue) for issue in result.warnings],
        }
    return {
        "status": "PREPARED_FOR_OPERATOR_REVIEW",
        "authority": "candidate_manifest_only",
        "authoritative": False,
        "integrity_hash": data["integrity_hash"],
        "integrity_check": "verified",
        "manifest": deepcopy(dict(data)),
    }


def canonical_manifest_from_dict(data: Mapping[str, Any]) -> dict[str, object]:
    result = validate_canonical_memory_manifest(data)
    if not result.ok:
        codes = ", ".join(issue.code for issue in result.errors)
        raise ValueError(f"invalid canonical memory manifest: {codes}")
    return deepcopy(dict(data))


def _validate_provenance(provenance: Mapping[str, Any], errors: list[ValidationIssue]) -> None:
    required = ("kind", "repo", "issue_number", "comment_id", "approval_ref")
    for field_name in required:
        _require(provenance, field_name, f"$.provenance.{field_name}", errors)
    if provenance.get("kind") not in SUPPORTED_PROVENANCE_KINDS:
        errors.append(_issue("$.provenance.kind", "unsupported_provenance", "provenance kind is unsupported"))
    if provenance.get("repo") != "alanua-Skeleton":
        errors.append(_issue("$.provenance.repo", "unsupported_provenance_repo", "provenance repo must be the public-safe repo token"))
    if provenance.get("issue_number") != 1194:
        errors.append(_issue("$.provenance.issue_number", "unsupported_provenance_issue", "provenance issue is unsupported"))
    if provenance.get("comment_id") != 4846756659:
        errors.append(_issue("$.provenance.comment_id", "unsupported_provenance_comment", "provenance comment is unsupported"))
    approval_ref = provenance.get("approval_ref")
    if not isinstance(approval_ref, str) or not _SAFE_TOKEN_RE.fullmatch(approval_ref):
        errors.append(_issue("$.provenance.approval_ref", "invalid_approval_ref", "approval ref must be a safe token"))


def _reject_private_or_raw_values(value: Any, path: str, errors: list[ValidationIssue]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.lower() in FORBIDDEN_FIELD_NAMES:
                errors.append(_issue(f"{path}.{key}", "forbidden_field", "manifest contains forbidden raw or private field"))
                continue
            _reject_private_or_raw_values(child, f"{path}.{key}", errors)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_private_or_raw_values(child, f"{path}[{index}]", errors)
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(fragment in lowered for fragment in FORBIDDEN_VALUE_FRAGMENTS):
            errors.append(_issue(path, "private_or_raw_value", "manifest contains private-looking or raw value"))
        return
    errors.append(_issue(path, "non_json_value", "manifest must contain JSON-safe values only"))


def _require(data: Mapping[str, Any], field_name: str, path: str, errors: list[ValidationIssue]) -> None:
    if field_name not in data:
        errors.append(_issue(path, "missing_required", "required field is missing"))


def _issue(path: str, code: str, message: str) -> ValidationIssue:
    return ValidationIssue(severity="error", path=path, code=code, message=message)


def _issue_to_dict(issue: ValidationIssue) -> dict[str, str]:
    return {
        "severity": issue.severity,
        "path": issue.path,
        "code": issue.code,
        "message": issue.message,
    }
