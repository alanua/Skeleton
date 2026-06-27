from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Mapping

PATCH_PROPOSAL_SCHEMA = "skeleton.private_project_memory.patch_proposal.v1"
PATCH_PROPOSAL_EVENT_SCHEMA = "skeleton.private_project_memory.patch_proposal_event.v1"

REQUIRED_PATCHPROPOSAL_FIELDS = frozenset(
    {
        "namespace",
        "project_id",
        "object_id",
        "entity_scope",
        "fact_type",
        "normalized_target",
        "source_evidence_hash",
        "dedupe_key",
        "idempotency_key",
        "proposed_value",
        "provenance_refs",
        "actor_ref",
        "reason_code",
        "approval_tier",
        "confirmed_via_exact_ref",
        "confirmed_canonical_revision",
    }
)

_DEDUPE_PREFIX = "memory-dedupe:v1:"
_IDEMPOTENCY_PREFIX = "memory-idempotency:v1:"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_UNSAFE_VALUE_MARKERS = (
    "/",
    "\\",
    "file:",
    ".sqlite",
    ".db",
    "github.com",
    "drive.google.com",
    "secret",
    "token",
    "password",
    "credential",
)


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


class MemoryPatchProposalError(ValueError):
    """Base exception for deterministic memory proposal failures."""


class MemoryPatchProposalValidationError(MemoryPatchProposalError):
    """Raised when a proposal is missing required, exact, or public-safe proof."""


class MemoryPatchProposalIdempotencyError(MemoryPatchProposalError):
    """Raised when an idempotency key is reused for different payload content."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_dedupe_key(proposal: Mapping[str, Any]) -> str:
    """Compute the only accepted namespace-scoped dedupe key."""
    parts = [
        _safe_token(proposal.get("namespace"), "namespace"),
        _safe_token(proposal.get("project_id"), "project_id"),
        _safe_token(proposal.get("object_id"), "object_id"),
        _safe_token(proposal.get("entity_scope"), "entity_scope"),
        _safe_token(proposal.get("fact_type"), "fact_type"),
        _safe_token(proposal.get("normalized_target"), "normalized_target"),
        _source_hash(proposal.get("source_evidence_hash")),
    ]
    return _DEDUPE_PREFIX + stable_hash(parts)


def canonical_idempotency_key(proposal: Mapping[str, Any]) -> str:
    """Return a deterministic idempotency key for the complete write payload."""
    payload = {
        key: proposal[key]
        for key in sorted(proposal)
        if key not in {"idempotency_key"}
    }
    return _IDEMPOTENCY_PREFIX + stable_hash(payload)


class MemoryPatchProposalRegistry:
    """Deterministic, fail-closed intake for canonical project memory writes."""

    def __init__(self) -> None:
        self._events_by_dedupe: dict[str, dict[str, object]] = {}
        self._payload_hash_by_dedupe: dict[str, str] = {}
        self._payload_hash_by_idempotency: dict[str, str] = {}
        self._event_by_idempotency: dict[str, dict[str, object]] = {}
        self._accepted_by_target: dict[tuple[str, str, str, str, str, str], dict[str, object]] = {}
        self._conflicts: list[dict[str, object]] = []
        self._sequence = 0

    def propose(self, proposal: Mapping[str, Any]) -> dict[str, object]:
        normalized = _validate_proposal(proposal)
        payload_hash = stable_hash(_payload_without_idempotency(normalized))
        idempotency_key = str(normalized["idempotency_key"])
        existing_for_idempotency = self.lookup_by_idempotency(idempotency_key)
        previous_payload_hash = self._payload_hash_by_idempotency.get(idempotency_key)
        if previous_payload_hash is not None and previous_payload_hash != payload_hash:
            raise MemoryPatchProposalIdempotencyError("idempotency key reused with different payload")
        if existing_for_idempotency is not None:
            duplicate = deepcopy(existing_for_idempotency)
            duplicate["status"] = "DUPLICATE_EXISTING"
            return duplicate

        dedupe_key = str(normalized["dedupe_key"])
        existing_for_dedupe = self._events_by_dedupe.get(dedupe_key)
        if existing_for_dedupe is not None:
            if self._payload_hash_by_dedupe.get(dedupe_key) == payload_hash:
                self._payload_hash_by_idempotency[idempotency_key] = payload_hash
                self._event_by_idempotency[idempotency_key] = existing_for_dedupe
                return deepcopy(existing_for_dedupe)
            conflict = self._record_conflict(normalized, existing_for_dedupe)
            result = PatchProposalResult(
                schema=PATCH_PROPOSAL_EVENT_SCHEMA,
                status="REVIEW_REQUIRED",
                event_ref=str(conflict["event_ref"]),
                canonical_ref=None,
                dedupe_key=dedupe_key,
                idempotency_key=idempotency_key,
                conflict_ref=str(conflict["conflict_ref"]),
                approval_ref=str(normalized["approval_ref"]),
                confirmed_canonical_revision=int(normalized["confirmed_canonical_revision"]),
            )
            event = asdict(result)
            self._payload_hash_by_idempotency[idempotency_key] = payload_hash
            self._event_by_idempotency[idempotency_key] = event
            return deepcopy(event)

        target_key = _target_key(normalized)
        existing_target = self._accepted_by_target.get(target_key)
        if existing_target is not None:
            conflict = self._record_conflict(normalized, existing_target)
            result = PatchProposalResult(
                schema=PATCH_PROPOSAL_EVENT_SCHEMA,
                status="REVIEW_REQUIRED",
                event_ref=str(conflict["event_ref"]),
                canonical_ref=None,
                dedupe_key=dedupe_key,
                idempotency_key=idempotency_key,
                conflict_ref=str(conflict["conflict_ref"]),
                approval_ref=str(normalized["approval_ref"]),
                confirmed_canonical_revision=int(normalized["confirmed_canonical_revision"]),
            )
            event = asdict(result)
            self._events_by_dedupe[dedupe_key] = event
            self._payload_hash_by_dedupe[dedupe_key] = payload_hash
            self._payload_hash_by_idempotency[idempotency_key] = payload_hash
            self._event_by_idempotency[idempotency_key] = event
            return deepcopy(event)

        event = self._record_acceptance(normalized)
        self._events_by_dedupe[dedupe_key] = event
        self._payload_hash_by_dedupe[dedupe_key] = payload_hash
        self._payload_hash_by_idempotency[idempotency_key] = payload_hash
        self._event_by_idempotency[idempotency_key] = event
        self._accepted_by_target[target_key] = event
        return deepcopy(event)

    def get_conflicts(self) -> list[dict[str, object]]:
        return deepcopy(self._conflicts)

    def lookup_by_idempotency(self, idempotency_key: str) -> dict[str, object] | None:
        """Return a bounded public event for a known idempotency key."""
        idempotency_key = _expected_key(
            idempotency_key,
            None,
            _IDEMPOTENCY_PREFIX,
            "idempotency_key",
        )
        event = self._event_by_idempotency.get(idempotency_key)
        return deepcopy(event) if event is not None else None

    def _record_acceptance(self, proposal: Mapping[str, Any]) -> dict[str, object]:
        self._sequence += 1
        event_ref = f"proposal-event-{self._sequence:06d}"
        canonical_ref = f"canonical-{proposal['namespace']}-{proposal['project_id']}-{self._sequence:06d}"
        result = PatchProposalResult(
            schema=PATCH_PROPOSAL_EVENT_SCHEMA,
            status="ACCEPTED",
            event_ref=event_ref,
            canonical_ref=canonical_ref,
            dedupe_key=str(proposal["dedupe_key"]),
            idempotency_key=str(proposal["idempotency_key"]),
            conflict_ref=None,
            approval_ref=str(proposal["approval_ref"]),
            confirmed_canonical_revision=int(proposal["confirmed_canonical_revision"]),
        )
        event = asdict(result)
        event["value_hash"] = stable_hash(proposal["proposed_value"])
        event["source_evidence_hash"] = proposal["source_evidence_hash"]
        return event

    def _record_conflict(
        self, proposal: Mapping[str, Any], existing_target: Mapping[str, object]
    ) -> dict[str, object]:
        self._sequence += 1
        conflict_ref = f"proposal-conflict-{self._sequence:06d}"
        conflict = {
            "schema": PATCH_PROPOSAL_EVENT_SCHEMA,
            "status": "REVIEW_REQUIRED",
            "event_ref": f"proposal-event-{self._sequence:06d}",
            "conflict_ref": conflict_ref,
            "namespace": proposal["namespace"],
            "project_id": proposal["project_id"],
            "dedupe_key": proposal["dedupe_key"],
            "existing_canonical_ref": existing_target["canonical_ref"],
            "existing_event_ref": existing_target["event_ref"],
            "reason_code": "same_target_distinct_evidence_or_value",
        }
        self._conflicts.append(conflict)
        return conflict


def _validate_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(proposal, Mapping):
        raise MemoryPatchProposalValidationError("proposal must be an object")
    missing = sorted(REQUIRED_PATCHPROPOSAL_FIELDS - set(proposal))
    if missing:
        raise MemoryPatchProposalValidationError("proposal missing required fields")
    normalized = dict(proposal)
    if normalized.get("schema", PATCH_PROPOSAL_SCHEMA) != PATCH_PROPOSAL_SCHEMA:
        raise MemoryPatchProposalValidationError("invalid proposal schema")

    for key in (
        "namespace",
        "project_id",
        "object_id",
        "entity_scope",
        "fact_type",
        "normalized_target",
        "actor_ref",
        "reason_code",
        "approval_tier",
    ):
        normalized[key] = _safe_token(normalized.get(key), key)

    normalized["source_evidence_hash"] = _source_hash(normalized.get("source_evidence_hash"))
    if not isinstance(normalized.get("confirmed_canonical_revision"), int) or isinstance(
        normalized.get("confirmed_canonical_revision"), bool
    ):
        raise MemoryPatchProposalValidationError("confirmed canonical revision must be integer")
    if int(normalized["confirmed_canonical_revision"]) < 0:
        raise MemoryPatchProposalValidationError("confirmed canonical revision must be non-negative")

    normalized["dedupe_key"] = _expected_key(
        normalized.get("dedupe_key"),
        canonical_dedupe_key(normalized),
        _DEDUPE_PREFIX,
        "dedupe_key",
    )
    normalized["idempotency_key"] = _expected_key(
        normalized.get("idempotency_key"),
        None,
        _IDEMPOTENCY_PREFIX,
        "idempotency_key",
    )
    normalized["approval_ref"] = _safe_token(normalized.get("approval_ref"), "approval_ref")
    _validate_exact_evidence(normalized)
    _reject_private_markers(normalized.get("proposed_value"), "proposed_value")
    return normalized


def _validate_exact_evidence(proposal: Mapping[str, Any]) -> None:
    if proposal["approval_tier"] in {"none", "semantic", "semantic_only"}:
        raise MemoryPatchProposalValidationError("explicit approval tier required")
    provenance_refs = proposal.get("provenance_refs")
    if not isinstance(provenance_refs, list) or not provenance_refs:
        raise MemoryPatchProposalValidationError("provenance refs required")
    confirmed_ref = proposal.get("confirmed_via_exact_ref")
    if not isinstance(confirmed_ref, str) or not _SAFE_TOKEN_RE.fullmatch(confirmed_ref):
        raise MemoryPatchProposalValidationError("exact confirmation ref required")

    exact_refs = []
    for ref in provenance_refs:
        if not isinstance(ref, Mapping):
            raise MemoryPatchProposalValidationError("provenance ref must be object")
        ref_id = _safe_token(ref.get("ref"), "provenance_ref")
        ref_kind = _safe_token(ref.get("kind"), "provenance_kind")
        evidence_hash = _source_hash(ref.get("evidence_hash"))
        if ref_kind == "semantic_only":
            continue
        if ref_kind == "exact_source" and evidence_hash == proposal["source_evidence_hash"]:
            exact_refs.append(ref_id)
    if confirmed_ref not in exact_refs:
        raise MemoryPatchProposalValidationError("semantic-only evidence cannot authorize write")


def _expected_key(raw: object, expected: str | None, prefix: str, name: str) -> str:
    if not isinstance(raw, str) or not raw.startswith(prefix) or not _HASH_RE.fullmatch(raw[len(prefix) :]):
        raise MemoryPatchProposalValidationError(f"malformed {name}")
    if expected is not None and raw != expected:
        raise MemoryPatchProposalValidationError(f"{name} does not match canonical payload")
    return raw


def _payload_without_idempotency(proposal: Mapping[str, Any]) -> dict[str, Any]:
    return {key: proposal[key] for key in sorted(proposal) if key != "idempotency_key"}


def _target_key(proposal: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        str(proposal["namespace"]),
        str(proposal["project_id"]),
        str(proposal["object_id"]),
        str(proposal["entity_scope"]),
        str(proposal["fact_type"]),
        str(proposal["normalized_target"]),
    )


def _safe_token(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise MemoryPatchProposalValidationError(f"invalid {name}")
    _reject_private_markers(value, name)
    return value


def _source_hash(value: object) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise MemoryPatchProposalValidationError("invalid source evidence hash")
    return value


def _reject_private_markers(value: object, name: str) -> None:
    serialized = canonical_json(value).lower()
    if any(marker in serialized for marker in _UNSAFE_VALUE_MARKERS):
        raise MemoryPatchProposalValidationError(f"{name} contains private-looking value")
