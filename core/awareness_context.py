from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from core.private_memory_history import content_hash, safe_token
from core.task_memory_context import TaskMemoryContextResult


AWARENESS_CONTEXT_SCHEMA = "skeleton.awareness_context.v1"
AWARENESS_CONTEXT_RECEIPT_SCHEMA = "skeleton.awareness_context.public_receipt.v1"
PRIVACY_BOUNDARY = "PRIVATE_RUNTIME_CONTEXT_PUBLIC_SAFE_RECEIPTS_ONLY"
DERIVATION_MODE = "ephemeral_read_only_derived_packet"
MAX_MEMORY_RECORDS = 10
MAX_PENDING_ITEMS = 10
MAX_CAPABILITY_RECORDS = 20
MAX_LABELS = 16
STALE_PENDING_AFTER_SECONDS = 7 * 24 * 60 * 60

FRESHNESS_LABELS = frozenset({"FRESH", "STALE", "UNKNOWN", "MIXED"})


class AwarenessContextError(ValueError):
    """Raised when an awareness context input cannot be safely assembled."""


@dataclass(frozen=True)
class AwarenessContextResult:
    packet: dict[str, Any]
    receipt: dict[str, Any]

    def public_receipt(self) -> dict[str, Any]:
        return dict(self.receipt)


def assemble_awareness_context(
    *,
    project_id: str,
    task_route: str,
    memory_context: TaskMemoryContextResult | Mapping[str, Any],
    pending_work: Mapping[str, Any],
    capability_truth: Mapping[str, Any],
    now: datetime | str | None = None,
    memory_limit: int = MAX_MEMORY_RECORDS,
    pending_limit: int = MAX_PENDING_ITEMS,
    capability_limit: int = MAX_CAPABILITY_RECORDS,
) -> AwarenessContextResult:
    """Assemble an ephemeral awareness packet from existing read-model outputs."""

    project = safe_token(project_id, "project_id")
    route = safe_token(task_route, "task_route")
    checked_at = _format_utc_timestamp(_coerce_now(now))
    memory_section = _memory_section(memory_context, _bounded_limit(memory_limit, MAX_MEMORY_RECORDS))
    pending_section = _pending_section(
        pending_work,
        _bounded_limit(pending_limit, MAX_PENDING_ITEMS),
        now=checked_at,
    )
    capability_section = _capability_section(
        capability_truth,
        _bounded_limit(capability_limit, MAX_CAPABILITY_RECORDS),
    )
    labels = _packet_labels(memory_section, pending_section, capability_section)
    sections = {
        "memory": memory_section,
        "pending_work": pending_section,
        "capability_truth": capability_section,
    }
    packet = {
        "schema": AWARENESS_CONTEXT_SCHEMA,
        "project_id": project,
        "task_route": route,
        "privacy_boundary": PRIVACY_BOUNDARY,
        "derivation_mode": DERIVATION_MODE,
        "checked_at": checked_at,
        "sections": sections,
        "labels": labels,
        "runtime_mutation_performed": False,
        "canonical_store_mutation_performed": False,
    }
    packet["awareness_hash"] = content_hash(packet)
    receipt = _public_receipt(packet)
    return AwarenessContextResult(packet=packet, receipt=receipt)


def _memory_section(
    memory_context: TaskMemoryContextResult | Mapping[str, Any],
    limit: int,
) -> dict[str, Any]:
    if isinstance(memory_context, TaskMemoryContextResult):
        receipt = memory_context.public_receipt()
        private_values = list(memory_context.private_values)
    elif isinstance(memory_context, Mapping):
        receipt_value = memory_context.get("receipt", memory_context)
        if not isinstance(receipt_value, Mapping):
            raise AwarenessContextError("memory_context receipt must be a mapping")
        receipt = dict(receipt_value)
        raw_private_values = memory_context.get("private_values", ())
        if not isinstance(raw_private_values, Sequence) or isinstance(raw_private_values, (str, bytes)):
            raise AwarenessContextError("memory_context private_values must be a sequence")
        private_values = [dict(item) for item in raw_private_values if isinstance(item, Mapping)]
    else:
        raise AwarenessContextError("memory_context must be a task memory result or mapping")

    records = _sequence_of_mappings(receipt.get("selected_records", ()), "selected_records")
    private_by_ref = {
        str(item["canonical_ref"]): item
        for item in private_values
        if isinstance(item.get("canonical_ref"), str)
    }
    selected: list[dict[str, Any]] = []
    seen_hashes: dict[str, str] = {}
    conflicts: list[str] = []
    for record in sorted(records, key=lambda item: str(item.get("canonical_ref", "")))[:limit]:
        canonical_ref = _public_ref(record.get("canonical_ref"), "canonical_ref")
        value_hash = _hash(record.get("value_hash"), "value_hash")
        if canonical_ref in seen_hashes and seen_hashes[canonical_ref] != value_hash:
            conflicts.append(canonical_ref)
        seen_hashes[canonical_ref] = value_hash
        item = {
            "canonical_ref": canonical_ref,
            "canonical_revision": _nonnegative_int(record.get("canonical_revision"), "canonical_revision"),
            "value_hash": value_hash,
            "public_text": record.get("public_text") if isinstance(record.get("public_text"), str) else None,
            "private_value": private_by_ref.get(canonical_ref, {}).get("value"),
            "epistemic_label": "OBSERVED",
        }
        selected.append(item)

    selected_count = _count_from(receipt, ("counts", "selected"), len(records))
    status = str(receipt.get("status", "UNKNOWN"))
    return _with_section_hash(
        {
            "status": status,
            "source_receipt_hash": content_hash(receipt),
            "source_context_hash": _optional_hash(receipt.get("context_hash")),
            "canonical_revision": _nonnegative_int(receipt.get("canonical_revision", 0), "canonical_revision"),
            "records": selected,
            "counts": {
                "source_selected": selected_count,
                "included": len(selected),
                "private_values_included": len([item for item in selected if item.get("private_value") is not None]),
            },
            "limits": {"records": limit},
            "truncated": bool(receipt.get("truncated", False)) or len(records) > limit,
            "freshness_label": "FRESH" if status == "DONE" else "UNKNOWN",
            "conflict_label": "CONFLICTS_PRESENT" if conflicts else "NONE",
            "epistemic_label": "OBSERVED" if selected else "UNKNOWN",
        }
    )


def _pending_section(pending_work: Mapping[str, Any], limit: int, *, now: str) -> dict[str, Any]:
    if pending_work.get("schema") != "skeleton.shared_pending_lifecycle_work.v1":
        raise AwarenessContextError("pending_work must be shared pending lifecycle work")
    items = _sequence_of_mappings(pending_work.get("items", ()), "items")
    selected = []
    seen_states: dict[str, str] = {}
    conflicts: list[str] = []
    freshnesses: list[str] = []
    checked_at = _coerce_now(now)
    for item in sorted(
        items,
        key=lambda value: (
            _nonnegative_int(value.get("updated_at", 0), "updated_at"),
            str(value.get("store_ref", "")),
            str(value.get("intake_id", "")),
        ),
    )[:limit]:
        intake_id = _public_ref(item.get("intake_id"), "intake_id")
        state = _label_token(item.get("state"), "state")
        prior_state = seen_states.get(intake_id)
        if prior_state is not None and prior_state != state:
            conflicts.append(intake_id)
        seen_states[intake_id] = state
        updated_at = _nonnegative_int(item.get("updated_at"), "updated_at")
        freshness = _pending_freshness(updated_at, checked_at)
        freshnesses.append(freshness)
        selected.append(
            {
                "store_ref": _label_token(item.get("store_ref"), "store_ref"),
                "intake_id": intake_id,
                "item_kind": _label_token(item.get("item_kind"), "item_kind"),
                "state": state,
                "blocker_reason": _label_token(item.get("blocker_reason"), "blocker_reason"),
                "next_action": _label_token(item.get("next_action"), "next_action"),
                "source_hash": _hash(item.get("source_hash"), "source_hash"),
                "updated_at": updated_at,
                "freshness_label": freshness,
                "epistemic_label": "OBSERVED",
            }
        )

    return _with_section_hash(
        {
            "source_receipt_hash": content_hash(pending_work),
            "items": selected,
            "counts": {
                "source_pending": _nonnegative_int(pending_work.get("pending_count", len(items)), "pending_count"),
                "included": len(selected),
            },
            "limits": {"items": limit},
            "truncated": len(items) > limit,
            "freshness_label": _aggregate_freshness(freshnesses),
            "conflict_label": "CONFLICTS_PRESENT" if conflicts else "NONE",
            "epistemic_label": "OBSERVED" if selected else "UNKNOWN",
        }
    )


def _capability_section(capability_truth: Mapping[str, Any], limit: int) -> dict[str, Any]:
    if capability_truth.get("schema") != "skeleton.capability_runtime_truth.v1":
        raise AwarenessContextError("capability_truth must be capability runtime truth")
    records = _sequence_of_mappings(capability_truth.get("capabilities", ()), "capabilities")
    selected = []
    freshnesses: list[str] = []
    conflict = False
    for record in sorted(records, key=lambda item: str(item.get("capability_id", "")))[:limit]:
        freshness = _freshness_label(record.get("freshness"))
        freshnesses.append(freshness)
        reason_codes = sorted(str(code) for code in record.get("reason_codes", ()) if isinstance(code, str))
        if bool(record.get("drift")) or "CONTRADICTORY_RUNTIME_EVIDENCE" in reason_codes:
            conflict = True
        selected.append(
            {
                "capability_id": _label_token(record.get("capability_id"), "capability_id"),
                "effective_status": _label_token(record.get("effective_status"), "effective_status"),
                "freshness_label": freshness,
                "conflict_label": "CONFLICTS_PRESENT"
                if bool(record.get("drift")) or "CONTRADICTORY_RUNTIME_EVIDENCE" in reason_codes
                else "NONE",
                "epistemic_label": "OBSERVED" if record.get("runtime_evidence") else "DECLARED",
                "reason_codes": reason_codes[:MAX_LABELS],
            }
        )

    return _with_section_hash(
        {
            "source_receipt_hash": content_hash(capability_truth),
            "records": selected,
            "counts": {"source_capabilities": len(records), "included": len(selected)},
            "limits": {"records": limit},
            "truncated": len(records) > limit,
            "freshness_label": _aggregate_freshness(freshnesses),
            "conflict_label": "CONFLICTS_PRESENT" if conflict else "NONE",
            "epistemic_label": _capability_epistemic(selected),
        }
    )


def _public_receipt(packet: Mapping[str, Any]) -> dict[str, Any]:
    sections = packet["sections"]
    receipt = {
        "schema": AWARENESS_CONTEXT_RECEIPT_SCHEMA,
        "status": "DONE",
        "project_id": packet["project_id"],
        "task_route": packet["task_route"],
        "privacy_boundary": packet["privacy_boundary"],
        "derivation_mode": packet["derivation_mode"],
        "checked_at": packet["checked_at"],
        "awareness_hash": packet["awareness_hash"],
        "section_hashes": {
            "memory": sections["memory"]["section_hash"],
            "pending_work": sections["pending_work"]["section_hash"],
            "capability_truth": sections["capability_truth"]["section_hash"],
        },
        "counts": {
            "memory_records": sections["memory"]["counts"]["included"],
            "pending_items": sections["pending_work"]["counts"]["included"],
            "capability_records": sections["capability_truth"]["counts"]["included"],
        },
        "limits": {
            "memory_records": sections["memory"]["limits"]["records"],
            "pending_items": sections["pending_work"]["limits"]["items"],
            "capability_records": sections["capability_truth"]["limits"]["records"],
        },
        "truncated": {
            "memory": sections["memory"]["truncated"],
            "pending_work": sections["pending_work"]["truncated"],
            "capability_truth": sections["capability_truth"]["truncated"],
        },
        "labels": packet["labels"],
        "public_safe": True,
        "private_payloads_included": False,
        "runtime_mutation_performed": False,
        "canonical_store_mutation_performed": False,
    }
    receipt["receipt_hash"] = content_hash(receipt)
    return receipt


def _packet_labels(*sections: Mapping[str, Any]) -> dict[str, str]:
    return {
        "freshness": _aggregate_freshness([str(section["freshness_label"]) for section in sections]),
        "conflict": "CONFLICTS_PRESENT"
        if any(section["conflict_label"] == "CONFLICTS_PRESENT" for section in sections)
        else "NONE",
        "epistemic": _aggregate_epistemic([str(section["epistemic_label"]) for section in sections]),
    }


def _with_section_hash(section: dict[str, Any]) -> dict[str, Any]:
    section["section_hash"] = content_hash(section)
    return section


def _bounded_limit(value: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise AwarenessContextError("limit must be an integer")
    return max(0, min(int(value), maximum))


def _sequence_of_mappings(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise AwarenessContextError(f"{field} must be a sequence")
    if not all(isinstance(item, Mapping) for item in value):
        raise AwarenessContextError(f"{field} must contain mappings")
    return list(value)


def _count_from(receipt: Mapping[str, Any], path: tuple[str, str], default: int) -> int:
    outer = receipt.get(path[0])
    if not isinstance(outer, Mapping):
        return default
    return _nonnegative_int(outer.get(path[1], default), path[1])


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AwarenessContextError(f"{field} must be a non-negative integer")
    return value


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise AwarenessContextError(f"{field} must be sha256")
    return value


def _optional_hash(value: Any) -> str | None:
    return None if value is None else _hash(value, "hash")


def _public_ref(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise AwarenessContextError(f"{field} must be public ref")
    text = value.strip()
    if not text or len(text) > 256 or text.startswith(("/", "~")) or "\\" in text or ".." in text:
        raise AwarenessContextError(f"{field} must be public ref")
    return text


def _label_token(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise AwarenessContextError(f"{field} must be a label")
    text = value.strip()
    if not text or len(text) > 160 or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-" for ch in text):
        raise AwarenessContextError(f"{field} must be a label")
    return text


def _freshness_label(value: Any) -> str:
    label = _label_token(value, "freshness")
    if label not in FRESHNESS_LABELS:
        raise AwarenessContextError("invalid freshness label")
    return label


def _pending_freshness(updated_at: int, now: datetime) -> str:
    if updated_at <= 0:
        return "UNKNOWN"
    age = int(now.timestamp()) - updated_at
    if age < 0:
        return "STALE"
    return "STALE" if age > STALE_PENDING_AFTER_SECONDS else "FRESH"


def _aggregate_freshness(values: Sequence[str]) -> str:
    labels = {_freshness_label(value) for value in values if value}
    if not labels:
        return "UNKNOWN"
    if labels == {"FRESH"}:
        return "FRESH"
    if labels == {"STALE"}:
        return "STALE"
    if labels == {"UNKNOWN"}:
        return "UNKNOWN"
    return "MIXED"


def _aggregate_epistemic(values: Sequence[str]) -> str:
    labels = {_label_token(value, "epistemic") for value in values if value}
    if not labels or labels == {"UNKNOWN"}:
        return "UNKNOWN"
    if "OBSERVED" in labels:
        return "OBSERVED"
    if "DERIVED" in labels:
        return "DERIVED"
    return "DECLARED"


def _capability_epistemic(records: Sequence[Mapping[str, Any]]) -> str:
    if not records:
        return "UNKNOWN"
    return _aggregate_epistemic([str(record["epistemic_label"]) for record in records])


def _coerce_now(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise AwarenessContextError("now must be an ISO UTC timestamp") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    raise AwarenessContextError("now must be a datetime, ISO timestamp string, or None")


def _format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
