from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from core.memory_patch_proposal import (
    PATCH_PROPOSAL_EVENT_SCHEMA,
    MemoryPatchProposalValidationError,
    _reject_private_markers,
    _safe_token,
    _source_hash,
    stable_hash,
)

OVERRIDE_EVENT_SCHEMA = "skeleton.private_project_memory.override_event.v1"
OVERRIDE_EVENT_TYPES = (
    "OVERRIDE_PROPOSAL",
    "OVERRIDE_APPROVAL",
    "OVERRIDE_ACTIVATION",
    "OVERRIDE_SUPERSESSION",
    "OVERRIDE_REVOCATION",
)


@dataclass(frozen=True)
class OverrideEvent:
    schema: str
    event_type: str
    override_ref: str
    event_ref: str
    namespace: str
    project_id: str
    object_id: str
    normalized_target: str
    actor_ref: str
    approval_ref: str | None
    evidence_hash: str | None
    supersedes_event_ref: str | None
    canonical_ref: str


class MemoryOverrideError(ValueError):
    """Base exception for override lifecycle failures."""


class MemoryOverrideValidationError(MemoryOverrideError):
    """Raised when override approval or evidence is absent or malformed."""


class MemoryOverrideRegistry:
    """Separate lifecycle model for approved as-built overrides."""

    def __init__(self) -> None:
        self._sequence = 0
        self._events_by_override: dict[str, list[dict[str, object]]] = {}
        self._active_by_target: dict[tuple[str, str, str, str], dict[str, object]] = {}

    def propose_override(self, proposal: Mapping[str, Any]) -> dict[str, object]:
        normalized = _validate_override_payload(proposal, require_approval=False)
        override_ref = f"override-{stable_hash(normalized)[:16]}"
        event = self._append_event(
            "OVERRIDE_PROPOSAL",
            override_ref=override_ref,
            payload=normalized,
            approval_ref=None,
            evidence_hash=None,
            supersedes_event_ref=None,
        )
        return deepcopy(event)

    def approve_override(
        self,
        override_ref: str,
        *,
        actor_ref: str,
        approval_ref: str,
        evidence_refs: list[Mapping[str, Any]],
    ) -> dict[str, object]:
        proposal = self._require_latest_payload(override_ref)
        actor_ref = _safe_token(actor_ref, "actor_ref")
        approval_ref = _safe_token(approval_ref, "approval_ref")
        evidence_hash = _exact_evidence_hash(evidence_refs)
        return self._append_event(
            "OVERRIDE_APPROVAL",
            override_ref=override_ref,
            payload={**proposal, "actor_ref": actor_ref},
            approval_ref=approval_ref,
            evidence_hash=evidence_hash,
            supersedes_event_ref=None,
        )

    def activate_override(self, override_ref: str) -> dict[str, object]:
        approval = self._latest_event(override_ref, "OVERRIDE_APPROVAL")
        if approval is None:
            raise MemoryOverrideValidationError("override activation requires approval")
        proposal = self._require_latest_payload(override_ref)
        target_key = _target_key(proposal)
        previous_active = self._active_by_target.get(target_key)
        event = self._append_event(
            "OVERRIDE_ACTIVATION",
            override_ref=override_ref,
            payload=proposal,
            approval_ref=str(approval["approval_ref"]),
            evidence_hash=str(approval["evidence_hash"]),
            supersedes_event_ref=(
                str(previous_active["event_ref"]) if previous_active is not None else None
            ),
        )
        self._active_by_target[target_key] = event
        return deepcopy(event)

    def supersede_override(
        self,
        override_ref: str,
        *,
        replacement_override_ref: str,
        actor_ref: str,
        approval_ref: str,
    ) -> dict[str, object]:
        active = self._latest_event(override_ref, "OVERRIDE_ACTIVATION")
        if active is None:
            raise MemoryOverrideValidationError("active override required for supersession")
        payload = self._require_latest_payload(override_ref)
        event = self._append_event(
            "OVERRIDE_SUPERSESSION",
            override_ref=override_ref,
            payload={**payload, "actor_ref": _safe_token(actor_ref, "actor_ref")},
            approval_ref=_safe_token(approval_ref, "approval_ref"),
            evidence_hash=str(active["evidence_hash"]),
            supersedes_event_ref=_safe_token(replacement_override_ref, "replacement_override_ref"),
        )
        self._active_by_target.pop(_target_key(payload), None)
        return deepcopy(event)

    def revoke_override(self, override_ref: str, *, actor_ref: str, approval_ref: str) -> dict[str, object]:
        active = self._latest_event(override_ref, "OVERRIDE_ACTIVATION")
        if active is None:
            raise MemoryOverrideValidationError("active override required for revocation")
        payload = self._require_latest_payload(override_ref)
        event = self._append_event(
            "OVERRIDE_REVOCATION",
            override_ref=override_ref,
            payload={**payload, "actor_ref": _safe_token(actor_ref, "actor_ref")},
            approval_ref=_safe_token(approval_ref, "approval_ref"),
            evidence_hash=str(active["evidence_hash"]),
            supersedes_event_ref=str(active["event_ref"]),
        )
        self._active_by_target.pop(_target_key(payload), None)
        return deepcopy(event)

    def get_active_fact(self, *, namespace: str, project_id: str, object_id: str, normalized_target: str) -> dict[str, object] | None:
        key = (
            _safe_token(namespace, "namespace"),
            _safe_token(project_id, "project_id"),
            _safe_token(object_id, "object_id"),
            _safe_token(normalized_target, "normalized_target"),
        )
        event = self._active_by_target.get(key)
        if event is None:
            return None
        payload = self._require_latest_payload(str(event["override_ref"]))
        return {
            "schema": PATCH_PROPOSAL_EVENT_SCHEMA,
            "status": "OVERRIDE_ACTIVE",
            "active_override_event_ref": event["event_ref"],
            "override_ref": event["override_ref"],
            "value": deepcopy(payload["override_value"]),
            "canonical_ref": payload["canonical_ref"],
            "canonical_value": deepcopy(payload["canonical_value"]),
        }

    def get_conflicts(self) -> list[dict[str, object]]:
        return []

    def get_override_history(self, override_ref: str) -> list[dict[str, object]]:
        override_ref = _safe_token(override_ref, "override_ref")
        return deepcopy(self._events_by_override.get(override_ref, []))

    def _append_event(
        self,
        event_type: str,
        *,
        override_ref: str,
        payload: Mapping[str, Any],
        approval_ref: str | None,
        evidence_hash: str | None,
        supersedes_event_ref: str | None,
    ) -> dict[str, object]:
        if event_type not in OVERRIDE_EVENT_TYPES:
            raise MemoryOverrideValidationError("unsupported override event type")
        self._sequence += 1
        event = OverrideEvent(
            schema=OVERRIDE_EVENT_SCHEMA,
            event_type=event_type,
            override_ref=_safe_token(override_ref, "override_ref"),
            event_ref=f"override-event-{self._sequence:06d}",
            namespace=str(payload["namespace"]),
            project_id=str(payload["project_id"]),
            object_id=str(payload["object_id"]),
            normalized_target=str(payload["normalized_target"]),
            actor_ref=str(payload["actor_ref"]),
            approval_ref=approval_ref,
            evidence_hash=evidence_hash,
            supersedes_event_ref=supersedes_event_ref,
            canonical_ref=str(payload["canonical_ref"]),
        )
        event_dict = asdict(event)
        event_dict["_payload"] = deepcopy(dict(payload))
        self._events_by_override.setdefault(event.override_ref, []).append(event_dict)
        public_event = deepcopy(event_dict)
        public_event.pop("_payload", None)
        return public_event

    def _require_latest_payload(self, override_ref: str) -> dict[str, Any]:
        override_ref = _safe_token(override_ref, "override_ref")
        history = self._events_by_override.get(override_ref)
        if not history:
            raise MemoryOverrideValidationError("unknown override")
        return deepcopy(history[0]["_payload"])  # type: ignore[index]

    def _latest_event(self, override_ref: str, event_type: str) -> dict[str, object] | None:
        override_ref = _safe_token(override_ref, "override_ref")
        for event in reversed(self._events_by_override.get(override_ref, [])):
            if event["event_type"] == event_type:
                return event
        return None


def _validate_override_payload(
    proposal: Mapping[str, Any], *, require_approval: bool
) -> dict[str, Any]:
    if not isinstance(proposal, Mapping):
        raise MemoryOverrideValidationError("override proposal must be object")
    required = {
        "namespace",
        "project_id",
        "object_id",
        "normalized_target",
        "canonical_ref",
        "canonical_value",
        "override_value",
        "actor_ref",
        "reason_code",
        "evidence_refs",
    }
    if require_approval:
        required.add("approval_ref")
    if required - set(proposal):
        raise MemoryOverrideValidationError("override proposal missing required field")
    normalized = dict(proposal)
    for key in ("namespace", "project_id", "object_id", "normalized_target", "canonical_ref", "actor_ref", "reason_code"):
        normalized[key] = _safe_token(normalized[key], key)
    if "approval_ref" in normalized:
        normalized["approval_ref"] = _safe_token(normalized["approval_ref"], "approval_ref")
    _reject_private_markers(normalized["canonical_value"], "canonical_value")
    _reject_private_markers(normalized["override_value"], "override_value")
    _exact_evidence_hash(normalized["evidence_refs"])
    return normalized


def _exact_evidence_hash(evidence_refs: object) -> str:
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise MemoryOverrideValidationError("override requires evidence refs")
    for ref in evidence_refs:
        if not isinstance(ref, Mapping):
            raise MemoryOverrideValidationError("override evidence ref must be object")
        try:
            if _safe_token(ref.get("kind"), "evidence_kind") == "exact_source":
                return _source_hash(ref.get("evidence_hash"))
        except MemoryPatchProposalValidationError as exc:
            raise MemoryOverrideValidationError(str(exc)) from exc
    raise MemoryOverrideValidationError("override requires exact source evidence")


def _target_key(payload: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(payload["namespace"]),
        str(payload["project_id"]),
        str(payload["object_id"]),
        str(payload["normalized_target"]),
    )
