from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


SUPPORTED_MEMORY_TYPES = frozenset(
    {
        "weak_chat_memory",
        "project_state",
        "canon_candidate",
        "confirmed_canon",
        "private_sensitive",
        "rejected_outdated",
    }
)

PUBLIC_GITHUB_ROUTES = frozenset(
    {
        "github_canon_candidate",
        "github_confirmed_canon",
    }
)


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    project_id: str
    memory_type: str
    source: str
    trust_level: str
    content: str
    status: str
    created_at: str
    public_safe: bool = False
    critique_present: bool = False
    operator_approved: bool = False
    changes_canon_or_instruction: bool = False


@dataclass(frozen=True)
class MemoryRouteResult:
    status: str
    target_route: str
    requires_operator_approval: bool
    audit_summary: str
    blocked_reason: Optional[str] = None


def classify_memory_record(record: MemoryRecord) -> str:
    """Return the deterministic memory type used for routing."""
    if record.source == "chatgpt_memory":
        return "weak_chat_memory"

    if record.memory_type not in SUPPORTED_MEMORY_TYPES:
        return "rejected_outdated"

    return record.memory_type


def route_memory_record(record: MemoryRecord) -> MemoryRouteResult:
    memory_type = classify_memory_record(record)

    if memory_type == "weak_chat_memory":
        return _accepted(record, "weak_chat_memory", "weak_cache", requires_operator_approval=False)

    if memory_type == "project_state":
        return _accepted(record, "project_state", "project_state", requires_operator_approval=False)

    if memory_type == "canon_candidate":
        if not record.public_safe:
            return _blocked(record, memory_type, "github_canon_candidate", "canon_candidate is not public_safe.")
        critique_block = _critique_block_reason(record, memory_type)
        if critique_block is not None:
            return _blocked(record, memory_type, "github_canon_candidate", critique_block)
        return _accepted(record, memory_type, "github_canon_candidate", requires_operator_approval=True)

    if memory_type == "confirmed_canon":
        if not record.public_safe:
            return _blocked(record, memory_type, "github_confirmed_canon", "confirmed_canon is not public_safe.")
        critique_block = _critique_block_reason(record, memory_type)
        if critique_block is not None:
            return _blocked(record, memory_type, "github_confirmed_canon", critique_block)
        if record.operator_approved is not True:
            return _blocked(
                record,
                memory_type,
                "github_confirmed_canon",
                "confirmed_canon requires explicit operator approval.",
                requires_operator_approval=True,
            )
        return _accepted(record, memory_type, "github_confirmed_canon", requires_operator_approval=True)

    if memory_type == "private_sensitive":
        return _blocked(
            record,
            memory_type,
            "private_sensitive",
            "private_sensitive records never route to public GitHub.",
        )

    if memory_type == "rejected_outdated":
        return _accepted(record, memory_type, "rejected_archive", requires_operator_approval=False)

    return _accepted(record, "rejected_outdated", "rejected_archive", requires_operator_approval=False)


def _critique_block_reason(record: MemoryRecord, memory_type: str) -> Optional[str]:
    if memory_type not in {"canon_candidate", "confirmed_canon"}:
        return None
    if record.changes_canon_or_instruction and not record.critique_present:
        return "canon/instruction changes require critique before routing."
    return None


def _accepted(
    record: MemoryRecord,
    memory_type: str,
    target_route: str,
    *,
    requires_operator_approval: bool,
) -> MemoryRouteResult:
    return MemoryRouteResult(
        status="accepted",
        target_route=target_route,
        requires_operator_approval=requires_operator_approval,
        audit_summary=_audit_summary(record, memory_type, target_route),
        blocked_reason=None,
    )


def _blocked(
    record: MemoryRecord,
    memory_type: str,
    target_route: str,
    blocked_reason: str,
    *,
    requires_operator_approval: bool = False,
) -> MemoryRouteResult:
    return MemoryRouteResult(
        status="blocked",
        target_route=target_route,
        requires_operator_approval=requires_operator_approval,
        audit_summary=_audit_summary(record, memory_type, target_route),
        blocked_reason=blocked_reason,
    )


def _audit_summary(record: MemoryRecord, memory_type: str, target_route: str) -> str:
    public_boundary = "public_safe" if record.public_safe else "not_public_safe"
    approval = "operator_approved" if record.operator_approved else "operator_approval_missing"
    critique = "critique_present" if record.critique_present else "critique_missing"
    return (
        f"record={record.id}; project={record.project_id}; source={record.source}; "
        f"type={memory_type}; route={target_route}; {public_boundary}; {approval}; {critique}"
    )
