from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.awareness_context import AwarenessContextResult, assemble_awareness_context
from core.private_memory_stack import PrivateMemoryStack
from core.task_memory_context import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_RECORDS,
    TASK_MEMORY_CONTEXT_NAMESPACES,
    TaskMemoryContextResult,
    build_task_memory_context,
)


AWARENESS_HYDRATION_SCHEMA = "skeleton.awareness_hydration.v1"
DEFAULT_MEMORY_PROFILE = "private_runtime"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:-]+")
_MIN_RELEVANCE_TOKEN_LENGTH = 3


class AwarenessHydrationError(ValueError):
    """Raised when injected awareness hydration inputs are invalid."""


@dataclass(frozen=True)
class AwarenessHydrationProviders:
    """Typed read-only inputs for awareness context hydration."""

    memory_stack: PrivateMemoryStack
    pending_work: Mapping[str, Any]
    capability_truth: Mapping[str, Any]


@dataclass(frozen=True)
class AwarenessHydrationRequest:
    project_id: str
    task_route: str
    task_body: str
    requested_capabilities: tuple[str, ...] = ()
    memory_profile: str = DEFAULT_MEMORY_PROFILE
    memory_namespaces: tuple[str, ...] = tuple(sorted(TASK_MEMORY_CONTEXT_NAMESPACES))
    memory_limit: int = MAX_CONTEXT_RECORDS
    memory_max_chars: int = MAX_CONTEXT_CHARS
    pending_limit: int = 10
    capability_limit: int = 20
    now: str | None = None


def hydrate_awareness_context(
    request: AwarenessHydrationRequest,
    providers: AwarenessHydrationProviders,
) -> AwarenessContextResult:
    """Hydrate the awareness assembler from injected read-only provider outputs."""

    if not isinstance(request, AwarenessHydrationRequest):
        raise AwarenessHydrationError("request must be an AwarenessHydrationRequest")
    if not isinstance(providers, AwarenessHydrationProviders):
        raise AwarenessHydrationError("providers must be AwarenessHydrationProviders")

    terms = _relevance_terms(request.task_body, request.requested_capabilities)
    query = _memory_query(terms, request.task_body)
    memory_context = build_task_memory_context(
        providers.memory_stack,
        project_id=request.project_id,
        task_route=request.task_route,
        profile=request.memory_profile,
        query=query,
        namespaces=request.memory_namespaces,
        required=False,
        limit=request.memory_limit,
        max_chars=request.memory_max_chars,
    )
    return assemble_awareness_context(
        project_id=request.project_id,
        task_route=request.task_route,
        memory_context=memory_context,
        pending_work=_relevant_pending_work(providers.pending_work, terms),
        capability_truth=_relevant_capability_truth(
            providers.capability_truth,
            terms,
            requested_capabilities=request.requested_capabilities,
        ),
        now=request.now,
        memory_limit=request.memory_limit,
        pending_limit=request.pending_limit,
        capability_limit=request.capability_limit,
    )


def hydrate_from_read_models(
    *,
    project_id: str,
    task_route: str,
    task_body: str,
    memory_context: TaskMemoryContextResult | Mapping[str, Any],
    pending_work: Mapping[str, Any],
    capability_truth: Mapping[str, Any],
    requested_capabilities: Sequence[str] = (),
    now: str | None = None,
    memory_limit: int = 10,
    pending_limit: int = 10,
    capability_limit: int = 20,
) -> AwarenessContextResult:
    """Assemble from already-built read models after relevance trimming."""

    terms = _relevance_terms(task_body, requested_capabilities)
    return assemble_awareness_context(
        project_id=project_id,
        task_route=task_route,
        memory_context=memory_context,
        pending_work=_relevant_pending_work(pending_work, terms),
        capability_truth=_relevant_capability_truth(
            capability_truth,
            terms,
            requested_capabilities=requested_capabilities,
        ),
        now=now,
        memory_limit=memory_limit,
        pending_limit=pending_limit,
        capability_limit=capability_limit,
    )


def _relevance_terms(*parts: object) -> frozenset[str]:
    terms: set[str] = set()
    for part in parts:
        if isinstance(part, str):
            values = (part,)
        elif isinstance(part, Sequence) and not isinstance(part, (bytes, bytearray)):
            values = tuple(str(item) for item in part if isinstance(item, str))
        else:
            values = ()
        for value in values:
            for token in _TOKEN_RE.findall(value.lower()):
                if len(token) >= _MIN_RELEVANCE_TOKEN_LENGTH:
                    terms.add(token)
                for segment in re.split(r"[_.:-]+", token):
                    if len(segment) >= _MIN_RELEVANCE_TOKEN_LENGTH:
                        terms.add(segment)
    return frozenset(terms)


def _memory_query(terms: frozenset[str], task_body: str) -> str:
    selected: list[str] = []
    length = 0
    for term in sorted(terms):
        next_length = length + len(term) + (1 if selected else 0)
        if next_length > 128:
            break
        selected.append(term)
        length = next_length
    if selected:
        return " ".join(selected)
    text = task_body.strip()
    return text if text else "summary"


def _relevant_pending_work(pending_work: Mapping[str, Any], terms: frozenset[str]) -> dict[str, Any]:
    if pending_work.get("schema") != "skeleton.shared_pending_lifecycle_work.v1":
        raise AwarenessHydrationError("pending_work must be shared pending lifecycle work")
    copied = copy.deepcopy(dict(pending_work))
    items = copied.get("items", ())
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise AwarenessHydrationError("pending_work items must be a sequence")
    selected = [item for item in items if isinstance(item, Mapping) and _matches_terms(item, terms)]
    copied["items"] = selected
    copied["pending_count"] = len(selected)
    copied["state_counts"] = _counts_by(selected, "state")
    copied["item_kind_counts"] = _counts_by(selected, "item_kind")
    copied["source_counts"] = _counts_by(selected, "store_ref")
    copied["public_safe"] = True
    copied["private_payloads_included"] = False
    copied["external_side_effects_executed"] = False
    return copied


def _relevant_capability_truth(
    capability_truth: Mapping[str, Any],
    terms: frozenset[str],
    *,
    requested_capabilities: Sequence[str],
) -> dict[str, Any]:
    if capability_truth.get("schema") != "skeleton.capability_runtime_truth.v1":
        raise AwarenessHydrationError("capability_truth must be capability runtime truth")
    copied = copy.deepcopy(dict(capability_truth))
    records = copied.get("capabilities", ())
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise AwarenessHydrationError("capability_truth capabilities must be a sequence")
    requested = {item for item in requested_capabilities if isinstance(item, str)}
    selected = [
        record
        for record in records
        if isinstance(record, Mapping)
        and (
            str(record.get("capability_id")) in requested
            or _matches_terms(record, terms)
        )
    ]
    copied["capabilities"] = selected
    copied["runtime_probe_performed"] = False
    copied["runtime_mutation_performed"] = False
    copied["summary"] = _capability_summary(selected)
    return copied


def _matches_terms(value: object, terms: frozenset[str]) -> bool:
    if not terms:
        return True
    haystack = _stringify_public(value).lower()
    return any(term in haystack for term in terms)


def _stringify_public(value: object) -> str:
    if isinstance(value, Mapping):
        return " ".join(_stringify_public(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(_stringify_public(child) for child in value)
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return ""


def _counts_by(items: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = item.get(field)
        if isinstance(value, str):
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _capability_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    def _count(field: str, value: str) -> int:
        return len([record for record in records if record.get(field) == value])

    return {
        "registry_capability_count": len(records),
        "registry_available_count": _count("declared_status", "available"),
        "typed_runtime_evidence_count": sum(
            len(record.get("runtime_evidence", ()))
            for record in records
            if isinstance(record.get("runtime_evidence", ()), Sequence)
        ),
        "fresh_runtime_evidence_count": sum(
            1
            for record in records
            for item in record.get("runtime_evidence", ())
            if isinstance(item, Mapping) and item.get("freshness") == "FRESH"
        ),
        "live_count": _count("effective_status", "LIVE"),
        "partial_count": _count("effective_status", "PARTIAL"),
        "contract_only_count": _count("effective_status", "CONTRACT_ONLY"),
        "blocked_count": _count("effective_status", "BLOCKED"),
        "legacy_count": _count("effective_status", "LEGACY"),
        "superseded_count": _count("effective_status", "SUPERSEDED"),
        "drift_count": len([record for record in records if bool(record.get("drift"))]),
    }
