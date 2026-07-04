from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any, Mapping

from core.memory_patch_common import (
    PATCH_PROPOSAL_EVENT_SCHEMA,
    MemoryPatchProposalIdempotencyError,
    PatchProposalResult,
    stable_hash,
    validate_proposal,
)


class MemoryPatchProposalRegistry:
    def __init__(self) -> None:
        self.by_dedupe: dict[str, dict[str, object]] = {}
        self.hash_by_dedupe: dict[str, str] = {}
        self.hash_by_idem: dict[str, str] = {}
        self.by_idem: dict[str, dict[str, object]] = {}
        self.by_target: dict[tuple[str, ...], dict[str, object]] = {}
        self.conflicts: list[dict[str, object]] = []
        self.sequence = 0

    def propose(self, proposal: Mapping[str, Any]) -> dict[str, object]:
        item = validate_proposal(proposal)
        payload_hash = stable_hash({k: item[k] for k in sorted(item) if k != "idempotency_key"})
        idem = str(item["idempotency_key"])
        previous_hash = self.hash_by_idem.get(idem)
        if previous_hash is not None and previous_hash != payload_hash:
            raise MemoryPatchProposalIdempotencyError("idempotency key reused with different payload")
        previous = self.by_idem.get(idem)
        if previous is not None:
            result = deepcopy(previous)
            result["status"] = "DUPLICATE_EXISTING"
            return result

        dedupe = str(item["dedupe_key"])
        previous = self.by_dedupe.get(dedupe)
        if previous is not None:
            if self.hash_by_dedupe.get(dedupe) == payload_hash:
                self._bind(idem, payload_hash, previous)
                return deepcopy(previous)
            return self._conflict(item, previous, payload_hash)

        target = tuple(str(item[k]) for k in (
            "namespace", "project_id", "object_id", "entity_scope", "fact_type", "normalized_target"
        ))
        previous = self.by_target.get(target)
        if previous is not None:
            result = self._conflict(item, previous, payload_hash)
            self.by_dedupe[dedupe] = deepcopy(result)
            self.hash_by_dedupe[dedupe] = payload_hash
            return result

        event = self._accept(item)
        self.by_dedupe[dedupe] = event
        self.hash_by_dedupe[dedupe] = payload_hash
        self._bind(idem, payload_hash, event)
        self.by_target[target] = event
        return deepcopy(event)

    def get_conflicts(self) -> list[dict[str, object]]:
        return deepcopy(self.conflicts)

    def lookup_by_idempotency_key(self, key: str) -> dict[str, object] | None:
        if not isinstance(key, str):
            return None
        event = self.by_idem.get(key)
        return deepcopy(event) if event is not None else None

    def _bind(self, key: str, payload_hash: str, event: Mapping[str, object]) -> None:
        self.hash_by_idem[key] = payload_hash
        self.by_idem[key] = dict(event)

    def _accept(self, item: Mapping[str, Any]) -> dict[str, object]:
        self.sequence += 1
        event = asdict(PatchProposalResult(
            schema=PATCH_PROPOSAL_EVENT_SCHEMA,
            status="ACCEPTED",
            event_ref=f"proposal-event-{self.sequence:06d}",
            canonical_ref=f"canonical-{item['namespace']}-{self.sequence:06d}",
            dedupe_key=str(item["dedupe_key"]),
            idempotency_key=str(item["idempotency_key"]),
            conflict_ref=None,
            approval_ref=str(item["approval_ref"]),
            confirmed_canonical_revision=int(item["confirmed_canonical_revision"]),
            canonical_write_performed=False,
            operator_approval_required=True,
        ))
        event["value_hash"] = stable_hash(item["proposed_value"])
        event["source_evidence_hash"] = item["source_evidence_hash"]
        return event

    def _conflict(
        self, item: Mapping[str, Any], previous: Mapping[str, object], payload_hash: str
    ) -> dict[str, object]:
        self.sequence += 1
        conflict = {
            "schema": PATCH_PROPOSAL_EVENT_SCHEMA,
            "status": "REVIEW_REQUIRED",
            "event_ref": f"proposal-event-{self.sequence:06d}",
            "conflict_ref": f"proposal-conflict-{self.sequence:06d}",
            "dedupe_key": item["dedupe_key"],
            "namespace": item["namespace"],
            "project_id": item["project_id"],
            "existing_canonical_ref": previous["canonical_ref"],
            "existing_event_ref": previous["event_ref"],
            "reason_code": "same_target_distinct_evidence_or_value",
        }
        self.conflicts.append(conflict)
        result = asdict(PatchProposalResult(
            schema=PATCH_PROPOSAL_EVENT_SCHEMA,
            status="REVIEW_REQUIRED",
            event_ref=str(conflict["event_ref"]),
            canonical_ref=None,
            dedupe_key=str(item["dedupe_key"]),
            idempotency_key=str(item["idempotency_key"]),
            conflict_ref=str(conflict["conflict_ref"]),
            approval_ref=str(item["approval_ref"]),
            confirmed_canonical_revision=int(item["confirmed_canonical_revision"]),
            canonical_write_performed=False,
            operator_approval_required=True,
        ))
        self._bind(str(item["idempotency_key"]), payload_hash, result)
        return deepcopy(result)
