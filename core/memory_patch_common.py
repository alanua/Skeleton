from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from core.memory_value_validation import MemoryValueError, validate_memory_value

PATCH_PROPOSAL_SCHEMA = "skeleton.private_project_memory.patch_proposal.v1"
PATCH_PROPOSAL_EVENT_SCHEMA = "skeleton.private_project_memory.patch_proposal_event.v1"
REQUIRED_FIELDS = frozenset(
    {
        "namespace", "project_id", "object_id", "entity_scope", "fact_type",
        "normalized_target", "source_evidence_hash", "dedupe_key", "idempotency_key",
        "proposed_value", "provenance_refs", "actor_ref", "reason_code",
        "approval_tier", "approval_ref", "confirmed_via_exact_ref",
        "confirmed_canonical_revision",
    }
)
DEDUPE_PREFIX = "memory-dedupe:v1:"
IDEMPOTENCY_PREFIX = "memory-idempotency:v1:"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True)
class PatchProposalResult:
    schema: str
    status: str
    event_ref: str
    canonical_ref: str | None
    dedupe_key: str
    idempotency_key: str
    conflict_ref: str | None
    approval_ref: str | None
    confirmed_canonical_revision: int
    canonical_write_performed: bool
    operator_approval_required: bool


class MemoryPatchProposalError(ValueError):
    pass


class MemoryPatchProposalValidationError(MemoryPatchProposalError):
    pass


class MemoryPatchProposalIdempotencyError(MemoryPatchProposalError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_dedupe_key(proposal: Mapping[str, Any]) -> str:
    parts = [
        safe_token(proposal.get("namespace"), "namespace"),
        safe_token(proposal.get("project_id"), "project_id"),
        safe_token(proposal.get("object_id"), "object_id"),
        safe_token(proposal.get("entity_scope"), "entity_scope"),
        safe_token(proposal.get("fact_type"), "fact_type"),
        safe_token(proposal.get("normalized_target"), "normalized_target"),
        source_hash(proposal.get("source_evidence_hash")),
    ]
    return DEDUPE_PREFIX + stable_hash(parts)


def canonical_idempotency_key(proposal: Mapping[str, Any]) -> str:
    payload = {key: proposal[key] for key in sorted(proposal) if key != "idempotency_key"}
    return IDEMPOTENCY_PREFIX + stable_hash(payload)


def validate_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(proposal, Mapping):
        raise MemoryPatchProposalValidationError("proposal must be an object")
    if REQUIRED_FIELDS - set(proposal):
        raise MemoryPatchProposalValidationError("proposal missing required fields")
    result = dict(proposal)
    if result.get("schema", PATCH_PROPOSAL_SCHEMA) != PATCH_PROPOSAL_SCHEMA:
        raise MemoryPatchProposalValidationError("invalid proposal schema")
    for key in (
        "namespace", "project_id", "object_id", "entity_scope", "fact_type",
        "normalized_target", "actor_ref", "reason_code", "approval_tier",
    ):
        result[key] = safe_token(result.get(key), key)
    result["source_evidence_hash"] = source_hash(result.get("source_evidence_hash"))
    revision = result.get("confirmed_canonical_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise MemoryPatchProposalValidationError("confirmed canonical revision must be non-negative integer")
    result["dedupe_key"] = expected_key(
        result.get("dedupe_key"), canonical_dedupe_key(result), DEDUPE_PREFIX, "dedupe_key"
    )
    result["idempotency_key"] = expected_key(
        result.get("idempotency_key"), None, IDEMPOTENCY_PREFIX, "idempotency_key"
    )
    result["approval_ref"] = safe_token(result.get("approval_ref"), "approval_ref")
    try:
        result["proposed_value"] = validate_memory_value(result.get("proposed_value"))
    except MemoryValueError as exc:
        raise MemoryPatchProposalValidationError(str(exc)) from exc
    validate_evidence(result)
    return result


def validate_evidence(proposal: Mapping[str, Any]) -> None:
    if proposal["approval_tier"] in {"none", "semantic", "semantic_only"}:
        raise MemoryPatchProposalValidationError("explicit approval tier required")
    refs = proposal.get("provenance_refs")
    if not isinstance(refs, list) or not refs:
        raise MemoryPatchProposalValidationError("provenance refs required")
    confirmed = safe_token(proposal.get("confirmed_via_exact_ref"), "confirmed_via_exact_ref")
    exact_refs: list[str] = []
    for item in refs:
        if not isinstance(item, Mapping):
            raise MemoryPatchProposalValidationError("provenance ref must be object")
        ref_id = safe_token(item.get("ref"), "provenance_ref")
        kind = safe_token(item.get("kind"), "provenance_kind")
        evidence = source_hash(item.get("evidence_hash"))
        if kind == "exact_source" and evidence == proposal["source_evidence_hash"]:
            exact_refs.append(ref_id)
    if confirmed not in exact_refs:
        raise MemoryPatchProposalValidationError("exact evidence required")


def expected_key(raw: object, expected: str | None, prefix: str, name: str) -> str:
    if not isinstance(raw, str) or not raw.startswith(prefix) or not _HASH_RE.fullmatch(raw[len(prefix):]):
        raise MemoryPatchProposalValidationError(f"malformed {name}")
    if expected is not None and raw != expected:
        raise MemoryPatchProposalValidationError(f"{name} does not match canonical payload")
    return raw


def safe_token(value: object, name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise MemoryPatchProposalValidationError(f"invalid {name}")
    return value


def source_hash(value: object) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise MemoryPatchProposalValidationError("invalid source evidence hash")
    return value
