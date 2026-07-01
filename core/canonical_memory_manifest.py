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

APPROVED_OPERATOR_RULE_SPEC: tuple[tuple[str, str, str], ...] = (
    (
        "rule-fast-autonomous-progress",
        "fast_autonomous_progress",
        "Work at a fast operational pace and continue through obvious next steps without waiting for repeated confirmation.",
    ),
    (
        "rule-independent-action",
        "independent_action",
        "Act independently inside already granted authority and established safety boundaries.",
    ),
    (
        "rule-low-procedural-overhead",
        "low_procedural_overhead",
        "Minimize procedural overhead, repetitive status checking, excessive caution, and unnecessary issue/comment churn.",
    ),
    (
        "rule-useful-work-over-paperwork",
        "useful_work_over_paperwork",
        "Prefer completing useful work over producing paperwork about the work.",
    ),
    (
        "rule-real-blockers-only",
        "real_blockers_only",
        "Ask or stop only for a real ambiguity, protected/high-risk approval boundary, unavailable access, or verified blocker.",
    ),
    (
        "rule-concise-result-updates",
        "concise_result_updates",
        "Keep operator updates short, concrete, and focused on result, blocker, verdict, or next action.",
    ),
    (
        "rule-status-fields",
        "status_fields",
        "Every operator-facing status must explicitly state both: what will happen next and whether the operator needs to do anything now.",
    ),
    (
        "rule-read-only-parallelization",
        "read_only_parallelization",
        "Parallelize safe read-only checks and preparation when that materially speeds delivery; serialize only conflicting or high-risk writes.",
    ),
    (
        "rule-incremental-memory-readiness",
        "incremental_memory_readiness",
        "Start using each approved memory layer as soon as that layer is verified ready; do not wait for all memory layers to be complete.",
    ),
    (
        "rule-sqlite-authority",
        "sqlite_authority",
        "Use canonical SQLite/Memory Gateway for authoritative durable facts as soon as available.",
    ),
    (
        "rule-graphify-relationships",
        "graphify_relationships",
        "Use Graphify for dependency/code relationship recall once its runtime index is verified.",
    ),
    (
        "rule-mempalace-semantic",
        "mempalace_semantic",
        "Use MemPalace for non-authoritative semantic recall once its runtime profile is verified, while keeping exact confirmation in canonical memory.",
    ),
)
APPROVED_OPERATOR_RULES_BY_ID = {
    rule_id: (category, statement)
    for rule_id, category, statement in APPROVED_OPERATOR_RULE_SPEC
}
APPROVED_OPERATOR_RULE_IDS = frozenset(APPROVED_OPERATOR_RULES_BY_ID)
APPROVED_OPERATOR_RULE_CATEGORIES = frozenset(
    category for _, category, _ in APPROVED_OPERATOR_RULE_SPEC
)
APPROVED_OPERATOR_RULE_COUNT = len(APPROVED_OPERATOR_RULE_SPEC)

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "namespace",
        "scope",
        "key",
        "record_type",
        "version",
        "authority",
        "privacy_classification",
        "provenance",
        "supersession",
        "record",
        "integrity_hash",
    }
)
_PROVENANCE_FIELDS = frozenset(
    {"kind", "repo", "issue_number", "comment_id", "approval_ref"}
)
_SUPERSESSION_FIELDS = frozenset({"status", "supersedes"})
_RECORD_FIELDS = frozenset({"preference_summary", "operating_rules"})
_RULE_FIELDS = frozenset({"id", "category", "statement"})

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
    serialized = json.dumps(
        candidate,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def validate_canonical_memory_manifest(data: Mapping[str, Any]) -> ValidationResult:
    errors: list[ValidationIssue] = []
    if not isinstance(data, Mapping):
        return ValidationResult(
            ok=False,
            errors=[_issue("$", "invalid_manifest", "manifest must be an object")],
        )

    _reject_unsupported_fields(data, _TOP_LEVEL_FIELDS, "$", errors)
    for field_name in _TOP_LEVEL_FIELDS:
        _require(data, field_name, f"$.{field_name}", errors)

    if data.get("schema") != CANONICAL_MEMORY_MANIFEST_SCHEMA:
        errors.append(
            _issue("$.schema", "unsupported_schema", "manifest schema is unsupported")
        )
    if data.get("namespace") not in SUPPORTED_CANONICAL_NAMESPACES:
        errors.append(
            _issue(
                "$.namespace",
                "unsupported_namespace",
                "canonical namespace is unsupported",
            )
        )
    if data.get("scope") != CANONICAL_OPERATOR_PREFERENCES_SCOPE:
        errors.append(
            _issue("$.scope", "unsupported_scope", "canonical scope is unsupported")
        )
    if data.get("key") != FAST_AUTONOMOUS_EXECUTION_KEY:
        errors.append(_issue("$.key", "unsupported_key", "canonical key is unsupported"))
    if data.get("record_type") != OPERATOR_WORKING_STYLE_RECORD_TYPE:
        errors.append(
            _issue(
                "$.record_type",
                "unsupported_record_type",
                "record type is unsupported",
            )
        )
    if data.get("authority") not in SUPPORTED_AUTHORITY:
        errors.append(
            _issue(
                "$.authority",
                "direct_sqlite_write_intent",
                "authority must remain candidate-only",
            )
        )
    if data.get("privacy_classification") not in SUPPORTED_PRIVACY_CLASSIFICATIONS:
        errors.append(
            _issue(
                "$.privacy_classification",
                "unsupported_privacy_classification",
                "privacy class is unsupported",
            )
        )
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        errors.append(
            _issue("$.version", "invalid_version", "version must be a positive integer")
        )

    provenance = data.get("provenance")
    if isinstance(provenance, Mapping):
        _validate_provenance(provenance, errors)
    elif "provenance" in data:
        errors.append(
            _issue(
                "$.provenance",
                "invalid_provenance",
                "provenance must be an object",
            )
        )

    supersession = data.get("supersession")
    if isinstance(supersession, Mapping):
        _validate_supersession(supersession, errors)
    elif "supersession" in data:
        errors.append(
            _issue(
                "$.supersession",
                "invalid_supersession",
                "supersession must be an object",
            )
        )

    record = data.get("record")
    if isinstance(record, Mapping):
        _validate_record(record, errors)
    elif "record" in data:
        errors.append(
            _issue("$.record", "invalid_record", "record must be an object")
        )

    integrity_hash = data.get("integrity_hash")
    if not isinstance(integrity_hash, str) or not _SHA256_RE.fullmatch(integrity_hash):
        errors.append(
            _issue(
                "$.integrity_hash",
                "invalid_integrity_hash",
                "integrity hash must be sha256 hex",
            )
        )
    elif integrity_hash != canonical_manifest_integrity_hash(data):
        errors.append(
            _issue(
                "$.integrity_hash",
                "integrity_hash_mismatch",
                "integrity hash does not match manifest",
            )
        )

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


def _validate_provenance(
    provenance: Mapping[str, Any],
    errors: list[ValidationIssue],
) -> None:
    _reject_unsupported_fields(
        provenance,
        _PROVENANCE_FIELDS,
        "$.provenance",
        errors,
    )
    for field_name in _PROVENANCE_FIELDS:
        _require(provenance, field_name, f"$.provenance.{field_name}", errors)
    if provenance.get("kind") not in SUPPORTED_PROVENANCE_KINDS:
        errors.append(
            _issue(
                "$.provenance.kind",
                "unsupported_provenance",
                "provenance kind is unsupported",
            )
        )
    if provenance.get("repo") != "alanua-Skeleton":
        errors.append(
            _issue(
                "$.provenance.repo",
                "unsupported_provenance_repo",
                "provenance repo must be the public-safe repo token",
            )
        )
    if provenance.get("issue_number") != 1194:
        errors.append(
            _issue(
                "$.provenance.issue_number",
                "unsupported_provenance_issue",
                "provenance issue is unsupported",
            )
        )
    if provenance.get("comment_id") != 4846756659:
        errors.append(
            _issue(
                "$.provenance.comment_id",
                "unsupported_provenance_comment",
                "provenance comment is unsupported",
            )
        )
    approval_ref = provenance.get("approval_ref")
    if not isinstance(approval_ref, str) or not _SAFE_TOKEN_RE.fullmatch(approval_ref):
        errors.append(
            _issue(
                "$.provenance.approval_ref",
                "invalid_approval_ref",
                "approval ref must be a safe token",
            )
        )


def _validate_supersession(
    supersession: Mapping[str, Any],
    errors: list[ValidationIssue],
) -> None:
    _reject_unsupported_fields(
        supersession,
        _SUPERSESSION_FIELDS,
        "$.supersession",
        errors,
    )
    for field_name in _SUPERSESSION_FIELDS:
        _require(supersession, field_name, f"$.supersession.{field_name}", errors)
    if supersession.get("status") not in SUPPORTED_SUPERSESSION_STATUS:
        errors.append(
            _issue(
                "$.supersession.status",
                "unsupported_supersession",
                "supersession status is unsupported",
            )
        )
    supersedes = supersession.get("supersedes")
    if not isinstance(supersedes, list):
        errors.append(
            _issue(
                "$.supersession.supersedes",
                "invalid_supersedes",
                "supersedes must be an array",
            )
        )
        return
    if len(supersedes) != len(set(supersedes)):
        errors.append(
            _issue(
                "$.supersession.supersedes",
                "duplicate_supersedes",
                "supersedes entries must be unique",
            )
        )
    for index, value in enumerate(supersedes):
        if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
            errors.append(
                _issue(
                    f"$.supersession.supersedes[{index}]",
                    "invalid_supersedes_entry",
                    "supersedes entries must be safe tokens",
                )
            )


def _validate_record(record: Mapping[str, Any], errors: list[ValidationIssue]) -> None:
    _reject_unsupported_fields(record, _RECORD_FIELDS, "$.record", errors)
    for field_name in _RECORD_FIELDS:
        _require(record, field_name, f"$.record.{field_name}", errors)

    preference_summary = record.get("preference_summary")
    if (
        not isinstance(preference_summary, str)
        or not preference_summary
        or len(preference_summary) > 240
    ):
        errors.append(
            _issue(
                "$.record.preference_summary",
                "invalid_preference_summary",
                "preference summary must be bounded text",
            )
        )

    operating_rules = record.get("operating_rules")
    if not isinstance(operating_rules, list):
        errors.append(
            _issue(
                "$.record.operating_rules",
                "invalid_operating_rules",
                "operating rules must be an array",
            )
        )
        return
    if len(operating_rules) != APPROVED_OPERATOR_RULE_COUNT:
        errors.append(
            _issue(
                "$.record.operating_rules",
                "operating_rule_count_mismatch",
                "operating rules must match the exact approved rule count",
            )
        )

    seen_ids: set[str] = set()
    seen_categories: set[str] = set()
    for index, rule in enumerate(operating_rules):
        path = f"$.record.operating_rules[{index}]"
        if not isinstance(rule, Mapping):
            errors.append(
                _issue(
                    path,
                    "invalid_operating_rule",
                    "operating rule must be an object",
                )
            )
            continue
        _reject_unsupported_fields(rule, _RULE_FIELDS, path, errors)
        for field_name in _RULE_FIELDS:
            _require(rule, field_name, f"{path}.{field_name}", errors)

        rule_id = rule.get("id")
        category = rule.get("category")
        statement = rule.get("statement")

        if not isinstance(rule_id, str) or not _SAFE_TOKEN_RE.fullmatch(rule_id):
            errors.append(
                _issue(
                    f"{path}.id",
                    "invalid_operating_rule_id",
                    "operating rule id must be a safe token",
                )
            )
        elif rule_id in seen_ids:
            errors.append(
                _issue(
                    f"{path}.id",
                    "duplicate_operating_rule_id",
                    "operating rule ids must be unique",
                )
            )
        else:
            seen_ids.add(rule_id)

        if not isinstance(category, str):
            errors.append(
                _issue(
                    f"{path}.category",
                    "invalid_operating_rule_category",
                    "operating rule category must be text",
                )
            )
        elif category in seen_categories:
            errors.append(
                _issue(
                    f"{path}.category",
                    "duplicate_operating_rule_category",
                    "operating rule categories must be unique",
                )
            )
        else:
            seen_categories.add(category)

        if not isinstance(statement, str) or not statement or len(statement) > 320:
            errors.append(
                _issue(
                    f"{path}.statement",
                    "invalid_operating_rule_statement",
                    "operating rule statement must be bounded text",
                )
            )

        if not isinstance(rule_id, str) or rule_id not in APPROVED_OPERATOR_RULES_BY_ID:
            if isinstance(rule_id, str):
                errors.append(
                    _issue(
                        f"{path}.id",
                        "unsupported_operating_rule_id",
                        "operating rule id is not approved",
                    )
                )
            continue
        expected_category, expected_statement = APPROVED_OPERATOR_RULES_BY_ID[rule_id]
        if category != expected_category:
            errors.append(
                _issue(
                    f"{path}.category",
                    "operating_rule_category_mismatch",
                    "operating rule category does not match the approved rule",
                )
            )
        if statement != expected_statement:
            errors.append(
                _issue(
                    f"{path}.statement",
                    "operating_rule_statement_mismatch",
                    "operating rule statement does not match the approved rule",
                )
            )

    missing_ids = APPROVED_OPERATOR_RULE_IDS - seen_ids
    if missing_ids:
        errors.append(
            _issue(
                "$.record.operating_rules",
                "missing_approved_operating_rule",
                "one or more approved operating rules are missing",
            )
        )
    extra_ids = seen_ids - APPROVED_OPERATOR_RULE_IDS
    if extra_ids:
        errors.append(
            _issue(
                "$.record.operating_rules",
                "extra_operating_rule",
                "one or more unapproved operating rules are present",
            )
        )
    if seen_categories != APPROVED_OPERATOR_RULE_CATEGORIES:
        errors.append(
            _issue(
                "$.record.operating_rules",
                "operating_rule_category_set_mismatch",
                "operating rule categories must match the approved set",
            )
        )


def _reject_unsupported_fields(
    data: Mapping[str, Any],
    allowed_fields: frozenset[str],
    path: str,
    errors: list[ValidationIssue],
) -> None:
    for field_name in data:
        if not isinstance(field_name, str) or field_name not in allowed_fields:
            errors.append(
                _issue(
                    f"{path}.{field_name}",
                    "unsupported_field",
                    "object contains an unsupported field",
                )
            )


def _reject_private_or_raw_values(
    value: Any,
    path: str,
    errors: list[ValidationIssue],
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.lower() in FORBIDDEN_FIELD_NAMES:
                errors.append(
                    _issue(
                        f"{path}.{key}",
                        "forbidden_field",
                        "manifest contains forbidden raw or private field",
                    )
                )
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
            errors.append(
                _issue(
                    path,
                    "private_or_raw_value",
                    "manifest contains private-looking or raw value",
                )
            )
        return
    errors.append(
        _issue(path, "non_json_value", "manifest must contain JSON-safe values only")
    )


def _require(
    data: Mapping[str, Any],
    field_name: str,
    path: str,
    errors: list[ValidationIssue],
) -> None:
    if field_name not in data:
        errors.append(_issue(path, "missing_required", "required field is missing"))


def _issue(path: str, code: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        severity="error",
        path=path,
        code=code,
        message=message,
    )


def _issue_to_dict(issue: ValidationIssue) -> dict[str, str]:
    return {
        "severity": issue.severity,
        "path": issue.path,
        "code": issue.code,
        "message": issue.message,
    }
