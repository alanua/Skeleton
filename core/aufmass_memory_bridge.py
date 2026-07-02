from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from core.private_memory_history import content_hash
from core.private_memory_stack import PrivateMemoryStack, PrivateMemoryStackError


AUFMASS_MEMORY_RECORD_SCHEMA = "skeleton.aufmass.memory_record.v1"
AUFMASS_MEMORY_CONTEXT_SCHEMA = "skeleton.aufmass.memory_context.v1"
AUFMASS_MEMORY_HISTORY_SCHEMA = "skeleton.aufmass.memory_history.v1"
AUFMASS_MEMORY_COMPARE_SCHEMA = "skeleton.aufmass.memory_compare.v1"
AUFMASS_REVIEW_DECISION_SCHEMA = "skeleton.aufmass.review_decision.v1"
AUFMASS_NAMESPACE = "aufmass"


class AufmassMemoryBridgeError(RuntimeError):
    """Raised when Aufmass private-memory work fails closed."""


def idempotency_key(project_ref: str, input_hash: str, transaction_ref: str) -> str:
    return content_hash(
        {
            "project_ref": project_ref,
            "input_hash": input_hash,
            "transaction_ref": transaction_ref,
        }
    )


class AufmassMemoryBridge:
    """Connect local Aufmass records to the active PrivateMemoryStack."""

    def __init__(self, private_root: str | None = None) -> None:
        self.stack = PrivateMemoryStack(private_root)

    def context(self, *, project_ref: str, query: str | None = None, limit: int = 5) -> dict[str, Any]:
        project_ref = _safe_token(project_ref, "project_ref")
        search_text = query or f"{project_ref} prior decisions warnings blockers calculations"
        semantic = self._safe_search(search_text, limit=limit)
        relations = self._safe_relations(project_ref, limit=limit)
        latest = self._get(_project_latest_fact_id(project_ref))
        return {
            "schema": AUFMASS_MEMORY_CONTEXT_SCHEMA,
            "project_ref": project_ref,
            "authoritative_for_calculation_inputs": False,
            "calculation_input_policy": "explicit_packet_only",
            "bounded_context": {
                "prior_project_decisions": _bounded_results(semantic, "decision"),
                "source_evidence_refs": _source_refs(latest),
                "previous_calculations": _bounded_results(semantic, "calculation"),
                "repeated_warnings": _bounded_results(semantic, "warning"),
                "unresolved_blockers": _bounded_results(semantic, "blocker"),
                "operator_approved_project_rules": _bounded_results(semantic, "operator approved rule"),
            },
            "derived_indexes": {
                "mempalace_results": len(semantic.get("results", [])),
                "graphify_results": len(relations.get("results", [])),
            },
            "latest_input_hash": latest.get("latest_input_hash") if isinstance(latest, dict) else None,
        }

    def history(self, *, project_ref: str, limit: int = 10) -> dict[str, Any]:
        project_ref = _safe_token(project_ref, "project_ref")
        index = self._get(_project_history_fact_id(project_ref)) or {"calculations": []}
        calculations = list(index.get("calculations", [])) if isinstance(index, Mapping) else []
        return {
            "schema": AUFMASS_MEMORY_HISTORY_SCHEMA,
            "project_ref": project_ref,
            "calculation_count": len(calculations),
            "calculations": calculations[-_bounded_limit(limit) :],
        }

    def compare(
        self,
        *,
        project_ref: str,
        current_record: Mapping[str, Any] | None = None,
        input_hash: str | None = None,
    ) -> dict[str, Any]:
        project_ref = _safe_token(project_ref, "project_ref")
        current = dict(current_record) if current_record is not None else None
        if current is None and input_hash:
            current = self._get(_calculation_fact_id(project_ref, input_hash))
        if current is None:
            return _compare_records(project_ref, {"input_hash": input_hash or "", "per_room_results": [], "warnings_blockers": []}, None)
        previous = self._latest_calculation(project_ref, exclude_input_hash=str(current.get("input_hash", "")))
        return _compare_records(project_ref, current, previous)

    def write_calculation(
        self,
        *,
        project_ref: str,
        normalized_input: Mapping[str, Any],
        review: Mapping[str, Any],
        audit: Mapping[str, Any],
        raw_result: Mapping[str, Any],
        actor_ref: str,
        reason_code: str,
        approval_ref: str,
        transaction_ref: str,
    ) -> dict[str, Any]:
        self._ensure_writable()
        project_ref = _safe_token(project_ref, "project_ref")
        actor_ref = _safe_token(actor_ref, "actor")
        reason_code = _safe_token(reason_code, "reason")
        approval_ref = _safe_token(approval_ref, "approval")
        transaction_ref = _safe_token(transaction_ref, "transaction")
        input_hash = str(audit["input_hash"])
        idem = idempotency_key(project_ref, input_hash, transaction_ref)
        idem_fact = f"idempotency.{idem}"
        existing = self._get(idem_fact)
        fingerprint = {
            "schema": "skeleton.aufmass.idempotency.v1",
            "project_ref": project_ref,
            "input_hash": input_hash,
            "transaction_ref": transaction_ref,
        }
        if existing is not None:
            if existing != fingerprint:
                raise AufmassMemoryBridgeError("idempotency key collision")
            current = self._get(_calculation_fact_id(project_ref, input_hash))
            if current is None:
                raise AufmassMemoryBridgeError("idempotent calculation record missing")
            return {
                "status": "DONE",
                "idempotent": True,
                "idempotency_key": idem,
                "canonical_revision": self.stack.status()["canonical_sqlite"]["canonical_revision"],
                "compare": self.compare(project_ref=project_ref, current_record=current),
            }

        record = _calculation_record(
            project_ref=project_ref,
            normalized_input=normalized_input,
            review=review,
            audit=audit,
            raw_result=raw_result,
            transaction_ref=transaction_ref,
            idempotency_key=idem,
        )
        prior_compare = self.compare(project_ref=project_ref, current_record=record)
        existing_history = self._get(_project_history_fact_id(project_ref))
        facts = _facts_for_record(project_ref, input_hash, record, fingerprint, existing_history)
        revision = 0
        for fact_id, value in facts:
            mutation = self.stack.put(
                namespace=AUFMASS_NAMESPACE,
                fact_id=fact_id,
                value=value,
                actor_ref=actor_ref,
                reason_code=reason_code,
                approval_ref=approval_ref,
                transaction_ref=f"{transaction_ref}.{fact_id}"[:128],
            )
            revision = int(mutation["canonical_revision"])
        return {
            "status": "DONE",
            "idempotent": False,
            "idempotency_key": idem,
            "canonical_revision": revision,
            "compare": prior_compare,
        }

    def write_review_decision(
        self,
        *,
        project_ref: str,
        decision_ref: str,
        decision_status: str,
        note: str,
        actor_ref: str,
        reason_code: str,
        approval_ref: str,
        transaction_ref: str,
        input_hash: str | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_writable()
        project_ref = _safe_token(project_ref, "project_ref")
        decision_ref = _safe_token(decision_ref, "decision_ref")
        decision_status = _safe_status(decision_status, "decision_status")
        transaction_ref = _safe_token(transaction_ref, "transaction")
        value = {
            "schema": AUFMASS_REVIEW_DECISION_SCHEMA,
            "project_ref": project_ref,
            "decision_ref": decision_ref,
            "decision_status": decision_status,
            "note": _bounded_text(note),
            "input_hash": input_hash,
            "room_id": _safe_token(room_id, "room_id") if room_id else None,
            "searchable_text": f"operator approved decision {decision_status} {note}",
            "tags": ["aufmass", "decision", decision_status],
            "relationships": [{"kind": "project_decision", "target": _project_node(project_ref)}],
        }
        if input_hash:
            value["relationships"].append({"kind": "reviews_output", "target": _calculation_node(project_ref, input_hash)})
        if room_id:
            value["relationships"].append({"kind": "reviews_room", "target": _room_node(project_ref, room_id)})
        mutation = self.stack.put(
            namespace=AUFMASS_NAMESPACE,
            fact_id=f"decision.{project_ref}.{decision_ref}",
            value=value,
            actor_ref=_safe_token(actor_ref, "actor"),
            reason_code=_safe_token(reason_code, "reason"),
            approval_ref=_safe_token(approval_ref, "approval"),
            transaction_ref=transaction_ref,
        )
        return {"schema": AUFMASS_REVIEW_DECISION_SCHEMA, "status": "DONE", "canonical_revision": mutation["canonical_revision"]}

    def _latest_calculation(self, project_ref: str, *, exclude_input_hash: str = "") -> dict[str, Any] | None:
        index = self._get(_project_history_fact_id(project_ref)) or {"calculations": []}
        calculations = list(index.get("calculations", [])) if isinstance(index, Mapping) else []
        for item in reversed(calculations):
            if not isinstance(item, Mapping):
                continue
            prior_hash = str(item.get("input_hash", ""))
            if prior_hash and prior_hash != exclude_input_hash:
                found = self._get(_calculation_fact_id(project_ref, prior_hash))
                if isinstance(found, dict):
                    return found
        return None

    def _get(self, fact_id: str) -> dict[str, Any] | None:
        try:
            return self.stack.get(namespace=AUFMASS_NAMESPACE, fact_id=fact_id)["value"]  # type: ignore[return-value]
        except Exception:
            return None

    def _safe_search(self, query: str, *, limit: int) -> dict[str, Any]:
        try:
            return self.stack.search(query=query, limit=_bounded_limit(limit))  # type: ignore[return-value]
        except Exception:
            return {"results": []}

    def _safe_relations(self, query: str, *, limit: int) -> dict[str, Any]:
        try:
            return self.stack.relations(query=query, limit=_bounded_limit(limit))  # type: ignore[return-value]
        except Exception:
            return {"results": []}

    def _ensure_writable(self) -> None:
        try:
            status = self.stack.status()
            if status["state"] in {"READY", "STALE"}:
                if status["state"] == "STALE":
                    self.stack.rebuild()
                return
            self.stack.init(import_manifest=False)
        except PrivateMemoryStackError as exc:
            raise AufmassMemoryBridgeError("private memory stack unavailable") from exc


def _calculation_record(
    *,
    project_ref: str,
    normalized_input: Mapping[str, Any],
    review: Mapping[str, Any],
    audit: Mapping[str, Any],
    raw_result: Mapping[str, Any],
    transaction_ref: str,
    idempotency_key: str,
) -> dict[str, Any]:
    rooms = list(review.get("rooms", []))
    blocked = list(review.get("blocked_rooms", []))
    summary = dict(review.get("summary", {}))
    source_refs = sorted(
        {
            str(ref)
            for room in normalized_input.get("rooms", [])
            if isinstance(room, Mapping)
            for ref in room.get("source_evidence_refs", [])
        }
    )
    return {
        "schema": AUFMASS_MEMORY_RECORD_SCHEMA,
        "project_ref": project_ref,
        "record_type": "calculation",
        "status": audit["status"],
        "input_hash": audit["input_hash"],
        "transaction_ref": transaction_ref,
        "idempotency_key": idempotency_key,
        "calculation_engine": "core.aufmass_engine.calculate_aufmass",
        "calculation_input_policy": "explicit_packet_only",
        "normalized_input": deepcopy(dict(normalized_input)),
        "calculation_summary": {
            "accepted_room_count": audit["accepted_room_count"],
            "blocked_room_count": audit["blocked_room_count"],
            "summary": summary,
        },
        "per_room_results": rooms,
        "warnings_blockers": blocked,
        "review_status": review["status"],
        "output_hashes": deepcopy(dict(audit["output_hashes"])),
        "source_evidence_refs": source_refs,
        "raw_result": deepcopy(dict(raw_result)),
        "operator_decisions": [],
        "searchable_text": _record_text(project_ref, audit, rooms, blocked, source_refs),
        "tags": ["aufmass", "calculation", str(audit["status"])],
        "relationships": [
            {"kind": "calculation_for_project", "target": _project_node(project_ref)},
            {"kind": "input_for_calculation", "target": _input_node(project_ref, str(audit["input_hash"]))},
            {"kind": "produces_output", "target": _output_node(project_ref, str(audit["input_hash"]))},
            {"kind": "has_review", "target": _review_node(project_ref, str(audit["input_hash"]))},
            *[{"kind": "uses_evidence", "target": _evidence_node(project_ref, ref)} for ref in source_refs[:20]],
            *[{"kind": "calculates_room", "target": _room_node(project_ref, str(room.get("room_id")))} for room in rooms[:20]],
        ],
    }


def _facts_for_record(
    project_ref: str,
    input_hash: str,
    record: Mapping[str, Any],
    fingerprint: Mapping[str, Any],
    existing_history: Mapping[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    history = {
        "schema": "skeleton.aufmass.project_history.v1",
        "project_ref": project_ref,
        "latest_input_hash": input_hash,
        "calculations": [],
        "searchable_text": f"project {project_ref} calculation history latest {input_hash}",
        "tags": ["aufmass", "history"],
        "relationships": [{"kind": "history_for_project", "target": _project_node(project_ref)}],
    }
    return [
        (f"idempotency.{record['idempotency_key']}", dict(fingerprint)),
        (_project_latest_fact_id(project_ref), _project_profile(project_ref, input_hash, record)),
        (_project_history_fact_id(project_ref), _merge_history(project_ref, history, existing_history, record)),
        (_input_fact_id(project_ref, input_hash), _input_record(project_ref, input_hash, record)),
        (_calculation_fact_id(project_ref, input_hash), dict(record)),
        (_output_fact_id(project_ref, input_hash), _output_record(project_ref, input_hash, record)),
        (_review_fact_id(project_ref, input_hash), _review_record(project_ref, input_hash, record)),
        *[
            (_room_fact_id(project_ref, str(room.get("room_id")), input_hash), _room_record(project_ref, input_hash, room, record))
            for room in record.get("per_room_results", [])
            if isinstance(room, Mapping)
        ],
        *[
            (_blocker_fact_id(project_ref, str(item.get("room_id")), input_hash), _blocker_record(project_ref, input_hash, item))
            for item in record.get("warnings_blockers", [])
            if isinstance(item, Mapping)
        ],
        *[
            (_evidence_fact_id(project_ref, str(ref)), _evidence_record(project_ref, str(ref), input_hash))
            for ref in record.get("source_evidence_refs", [])
        ],
    ]


def _merge_history(
    project_ref: str,
    new_history: dict[str, Any],
    existing_history: Mapping[str, Any] | None,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    calculations = []
    if isinstance(existing_history, Mapping):
        calculations = [dict(item) for item in existing_history.get("calculations", []) if isinstance(item, Mapping)]
    next_item = {
        "input_hash": record["input_hash"],
        "status": record["status"],
        "accepted_room_count": record["calculation_summary"]["accepted_room_count"],
        "blocked_room_count": record["calculation_summary"]["blocked_room_count"],
    }
    calculations = [item for item in calculations if item.get("input_hash") != next_item["input_hash"]]
    calculations.append(next_item)
    new_history["calculations"] = calculations[-50:]
    new_history["calculation_count"] = len(new_history["calculations"])
    new_history["project_ref"] = project_ref
    return new_history


def _compare_records(project_ref: str, current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> dict[str, Any]:
    if previous is None:
        return {
            "schema": AUFMASS_MEMORY_COMPARE_SCHEMA,
            "project_ref": project_ref,
            "baseline": "none",
            "changed_rooms": [],
            "changed_quantities": [],
            "repeated_blockers": [],
            "unchanged_results": [],
        }
    current_rooms = {str(row.get("room_id")): row for row in current.get("per_room_results", []) if isinstance(row, Mapping)}
    previous_rooms = {str(row.get("room_id")): row for row in previous.get("per_room_results", []) if isinstance(row, Mapping)}
    changed_rooms = sorted(room for room in set(current_rooms) | set(previous_rooms) if current_rooms.get(room) != previous_rooms.get(room))
    fields = ("floor_area", "ceiling_area", "perimeter", "gross_wall_area", "openings_area", "net_wall_area", "volume")
    changed_quantities = []
    for room in changed_rooms:
        before = previous_rooms.get(room, {})
        after = current_rooms.get(room, {})
        for field in fields:
            if before.get(field) != after.get(field):
                changed_quantities.append({"room_id": room, "field": field})
    current_blockers = {str(item.get("room_id")): str(item.get("reason")) for item in current.get("warnings_blockers", []) if isinstance(item, Mapping)}
    previous_blockers = {str(item.get("room_id")): str(item.get("reason")) for item in previous.get("warnings_blockers", []) if isinstance(item, Mapping)}
    repeated = [
        {"room_id": room, "reason": current_blockers[room]}
        for room in sorted(current_blockers)
        if previous_blockers.get(room) == current_blockers[room]
    ]
    unchanged = sorted(room for room in current_rooms if room in previous_rooms and current_rooms[room] == previous_rooms[room])
    return {
        "schema": AUFMASS_MEMORY_COMPARE_SCHEMA,
        "project_ref": project_ref,
        "baseline": str(previous.get("input_hash")),
        "changed_rooms": changed_rooms,
        "changed_quantities": changed_quantities,
        "repeated_blockers": repeated,
        "unchanged_results": unchanged,
    }


def _project_profile(project_ref: str, input_hash: str, record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": AUFMASS_MEMORY_RECORD_SCHEMA,
        "project_ref": project_ref,
        "record_type": "project_profile",
        "latest_input_hash": input_hash,
        "review_status": record["review_status"],
        "source_evidence_refs": list(record.get("source_evidence_refs", [])),
        "searchable_text": f"aufmass project {project_ref} profile review {record['review_status']}",
        "tags": ["aufmass", "project"],
        "relationships": [{"kind": "project_latest_calculation", "target": _calculation_node(project_ref, input_hash)}],
    }


def _input_record(project_ref: str, input_hash: str, record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": AUFMASS_MEMORY_RECORD_SCHEMA,
        "project_ref": project_ref,
        "record_type": "normalized_input",
        "input_hash": input_hash,
        "normalized_input": deepcopy(record["normalized_input"]),
        "searchable_text": f"aufmass normalized input {project_ref} {input_hash}",
        "tags": ["aufmass", "input"],
        "relationships": [{"kind": "input_for_project", "target": _project_node(project_ref)}],
    }


def _output_record(project_ref: str, input_hash: str, record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": AUFMASS_MEMORY_RECORD_SCHEMA,
        "project_ref": project_ref,
        "record_type": "output_hashes",
        "input_hash": input_hash,
        "output_hashes": deepcopy(record["output_hashes"]),
        "searchable_text": f"aufmass output hashes {project_ref} {input_hash}",
        "tags": ["aufmass", "output"],
        "relationships": [{"kind": "output_for_calculation", "target": _calculation_node(project_ref, input_hash)}],
    }


def _review_record(project_ref: str, input_hash: str, record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": AUFMASS_MEMORY_RECORD_SCHEMA,
        "project_ref": project_ref,
        "record_type": "review_status",
        "input_hash": input_hash,
        "review_status": record["review_status"],
        "searchable_text": f"aufmass review {record['review_status']} {project_ref}",
        "tags": ["aufmass", "review", str(record["review_status"])],
        "relationships": [{"kind": "reviews_calculation", "target": _calculation_node(project_ref, input_hash)}],
    }


def _room_record(project_ref: str, input_hash: str, room: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    room_id = str(room.get("room_id"))
    return {
        "schema": AUFMASS_MEMORY_RECORD_SCHEMA,
        "project_ref": project_ref,
        "record_type": "room_result",
        "input_hash": input_hash,
        "room_id": room_id,
        "result": deepcopy(dict(room)),
        "searchable_text": f"aufmass room calculation {project_ref} {room_id}",
        "tags": ["aufmass", "room"],
        "relationships": [
            {"kind": "room_in_project", "target": _project_node(project_ref)},
            {"kind": "room_in_calculation", "target": _calculation_node(project_ref, input_hash)},
        ],
    }


def _blocker_record(project_ref: str, input_hash: str, item: Mapping[str, Any]) -> dict[str, Any]:
    room_id = str(item.get("room_id"))
    reason = str(item.get("reason"))
    return {
        "schema": AUFMASS_MEMORY_RECORD_SCHEMA,
        "project_ref": project_ref,
        "record_type": "warning_blocker",
        "input_hash": input_hash,
        "room_id": room_id,
        "status": "blocked",
        "reason": reason,
        "searchable_text": f"aufmass blocker warning {project_ref} {room_id} {reason}",
        "tags": ["aufmass", "blocker", "blocked"],
        "relationships": [{"kind": "blocks_room", "target": _room_node(project_ref, room_id)}],
    }


def _evidence_record(project_ref: str, source_ref: str, input_hash: str) -> dict[str, Any]:
    return {
        "schema": AUFMASS_MEMORY_RECORD_SCHEMA,
        "project_ref": project_ref,
        "record_type": "source_evidence_ref",
        "source_evidence_ref": source_ref,
        "input_hash": input_hash,
        "searchable_text": f"aufmass source evidence {project_ref} {source_ref}",
        "tags": ["aufmass", "evidence"],
        "relationships": [
            {"kind": "evidence_for_project", "target": _project_node(project_ref)},
            {"kind": "evidence_for_calculation", "target": _calculation_node(project_ref, input_hash)},
        ],
    }


def _record_text(project_ref: str, audit: Mapping[str, Any], rooms: list[Any], blocked: list[Any], source_refs: list[str]) -> str:
    room_ids = " ".join(str(room.get("room_id")) for room in rooms if isinstance(room, Mapping))
    blockers = " ".join(str(item.get("reason")) for item in blocked if isinstance(item, Mapping))
    return f"aufmass calculation project {project_ref} status {audit['status']} rooms {room_ids} warnings blockers {blockers} evidence {' '.join(source_refs)}"


def _bounded_results(search: Mapping[str, Any], marker: str) -> list[dict[str, Any]]:
    results = []
    for item in search.get("results", []):
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("bounded_text", ""))
        if marker and marker.lower() not in text.lower():
            continue
        results.append(
            {
                "canonical_ref": item.get("canonical_ref"),
                "canonical_revision": item.get("canonical_revision"),
                "bounded_text": text[:240],
            }
        )
        if len(results) >= 5:
            break
    return results


def _source_refs(latest: Any) -> list[str]:
    if not isinstance(latest, Mapping):
        return []
    return [str(item) for item in latest.get("source_evidence_refs", [])[:20]]


def _bounded_limit(value: int) -> int:
    return max(1, min(int(value), 10))


def _bounded_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AufmassMemoryBridgeError("note is required")
    return value.strip()[:500]


def _safe_token(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise AufmassMemoryBridgeError(f"{name} must be text")
    value = value.strip()
    if not value or len(value) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-" for ch in value):
        raise AufmassMemoryBridgeError(f"invalid {name}")
    return value


def _safe_status(value: Any, name: str) -> str:
    status = _safe_token(value, name)
    if status not in {"confirmed", "estimated_review", "accepted_input", "blocked", "operator_approved"}:
        raise AufmassMemoryBridgeError(f"invalid {name}")
    return status


def _project_node(project_ref: str) -> str:
    return f"project:{project_ref}"


def _input_node(project_ref: str, input_hash: str) -> str:
    return f"input:{project_ref}:{input_hash[:24]}"


def _calculation_node(project_ref: str, input_hash: str) -> str:
    return f"calculation:{project_ref}:{input_hash[:24]}"


def _output_node(project_ref: str, input_hash: str) -> str:
    return f"output:{project_ref}:{input_hash[:24]}"


def _review_node(project_ref: str, input_hash: str) -> str:
    return f"review:{project_ref}:{input_hash[:24]}"


def _room_node(project_ref: str, room_id: str) -> str:
    return f"room:{project_ref}:{room_id}"


def _evidence_node(project_ref: str, ref: str) -> str:
    return f"evidence:{project_ref}:{ref}"


def _project_latest_fact_id(project_ref: str) -> str:
    return f"project.{project_ref}"


def _project_history_fact_id(project_ref: str) -> str:
    return f"history.{project_ref}"


def _calculation_fact_id(project_ref: str, input_hash: str) -> str:
    return f"calculation.{project_ref}.{input_hash[:24]}"


def _input_fact_id(project_ref: str, input_hash: str) -> str:
    return f"input.{project_ref}.{input_hash[:24]}"


def _output_fact_id(project_ref: str, input_hash: str) -> str:
    return f"output.{project_ref}.{input_hash[:24]}"


def _review_fact_id(project_ref: str, input_hash: str) -> str:
    return f"review.{project_ref}.{input_hash[:24]}"


def _room_fact_id(project_ref: str, room_id: str, input_hash: str) -> str:
    return f"room.{project_ref}.{room_id}.{input_hash[:12]}"


def _blocker_fact_id(project_ref: str, room_id: str, input_hash: str) -> str:
    return f"blocker.{project_ref}.{room_id}.{input_hash[:12]}"


def _evidence_fact_id(project_ref: str, source_ref: str) -> str:
    return f"evidence.{project_ref}.{source_ref}"
