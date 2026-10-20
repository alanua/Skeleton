from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Final

from core.audit_ledger import validate_public_safe_payload


DOMAIN_CASE_EVENT_SCHEMA: Final = "skeleton.domain_case_event.v1"
CASE_TIMELINE_SCHEMA: Final = "skeleton.case_timeline_read_model.v1"
DASHBOARD_CASES_SCHEMA: Final = "skeleton.dashboard_case_aggregate.v1"

PROVIDER_AREAS: Final = frozenset(
    {"mail", "documents", "finance", "gewerbe", "scheduler", "development"}
)
REF_KINDS: Final = frozenset(
    {
        "case",
        "mail",
        "document",
        "finance",
        "gewerbe",
        "schedule",
        "occurrence",
        "loop",
        "runner_continuation",
        "development",
    }
)
EDGE_CONFIDENCE: Final = frozenset({"explicit", "inferred", "uncertain"})
DEPENDENCY_STATES: Final = frozenset({"open", "satisfied", "blocked", "needs_operator"})
NEXT_ACTION_STATES: Final = frozenset(
    {"none", "ready", "waiting_dependency", "needs_operator", "blocked"}
)

_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,191}$")
_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;_()/#+-]{0,239}$")
_MAX_EVENTS = 256
_MAX_REFS_PER_EVENT = 24
_MAX_EDGES_PER_EVENT = 32
_MAX_DEPENDENCIES_PER_EVENT = 16


class DomainCaseTimelineError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class DomainRef:
    kind: str
    ref: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DomainRef":
        if not isinstance(value, Mapping):
            raise DomainCaseTimelineError("INVALID_REF", "ref must be an object")
        if set(value) != {"kind", "ref"}:
            raise DomainCaseTimelineError("INVALID_REF_FIELDS", "ref fields are invalid")
        kind = _enum(value.get("kind"), REF_KINDS, "ref.kind")
        ref = _safe_ref(value.get("ref"), "ref.ref")
        return cls(kind=kind, ref=ref)

    def to_mapping(self) -> dict[str, str]:
        return {"kind": self.kind, "ref": self.ref}


@dataclass(frozen=True)
class DomainEdge:
    source_ref: DomainRef
    target_ref: DomainRef
    relationship: str
    confidence: str = "explicit"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DomainEdge":
        if not isinstance(value, Mapping):
            raise DomainCaseTimelineError("INVALID_EDGE", "edge must be an object")
        required = {"source_ref", "target_ref", "relationship", "confidence"}
        if set(value) != required:
            raise DomainCaseTimelineError("INVALID_EDGE_FIELDS", "edge fields are invalid")
        return cls(
            source_ref=DomainRef.from_mapping(_mapping(value.get("source_ref"), "source_ref")),
            target_ref=DomainRef.from_mapping(_mapping(value.get("target_ref"), "target_ref")),
            relationship=_safe_text(value.get("relationship"), "relationship"),
            confidence=_enum(value.get("confidence"), EDGE_CONFIDENCE, "confidence"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "source_ref": self.source_ref.to_mapping(),
            "target_ref": self.target_ref.to_mapping(),
            "relationship": self.relationship,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class DomainDependency:
    dependency_ref: DomainRef
    state: str
    reason: str
    confidence: str = "explicit"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DomainDependency":
        if not isinstance(value, Mapping):
            raise DomainCaseTimelineError("INVALID_DEPENDENCY", "dependency must be an object")
        required = {"dependency_ref", "state", "reason", "confidence"}
        if set(value) != required:
            raise DomainCaseTimelineError("INVALID_DEPENDENCY_FIELDS", "dependency fields are invalid")
        return cls(
            dependency_ref=DomainRef.from_mapping(_mapping(value.get("dependency_ref"), "dependency_ref")),
            state=_enum(value.get("state"), DEPENDENCY_STATES, "dependency.state"),
            reason=_safe_text(value.get("reason"), "dependency.reason"),
            confidence=_enum(value.get("confidence"), EDGE_CONFIDENCE, "dependency.confidence"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "dependency_ref": self.dependency_ref.to_mapping(),
            "state": self.state,
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class DomainNextAction:
    state: str
    action_ref: DomainRef | None
    reason: str
    confidence: str = "explicit"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "DomainNextAction":
        if value is None:
            return cls(state="none", action_ref=None, reason="no_next_action")
        if not isinstance(value, Mapping):
            raise DomainCaseTimelineError("INVALID_NEXT_ACTION", "next_action must be an object")
        required = {"state", "action_ref", "reason", "confidence"}
        if set(value) != required:
            raise DomainCaseTimelineError("INVALID_NEXT_ACTION_FIELDS", "next_action fields are invalid")
        action_value = value.get("action_ref")
        return cls(
            state=_enum(value.get("state"), NEXT_ACTION_STATES, "next_action.state"),
            action_ref=None
            if action_value is None
            else DomainRef.from_mapping(_mapping(action_value, "next_action.action_ref")),
            reason=_safe_text(value.get("reason"), "next_action.reason"),
            confidence=_enum(value.get("confidence"), EDGE_CONFIDENCE, "next_action.confidence"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "state": self.state,
            "action_ref": None if self.action_ref is None else self.action_ref.to_mapping(),
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class DomainCaseEvent:
    event_id: str
    occurred_at: int
    provider_area: str
    event_type: str
    case_ref: str | None
    refs: tuple[DomainRef, ...]
    edges: tuple[DomainEdge, ...]
    dependencies: tuple[DomainDependency, ...]
    next_action: DomainNextAction
    public_summary: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DomainCaseEvent":
        if not isinstance(value, Mapping):
            raise DomainCaseTimelineError("INVALID_EVENT", "event must be an object")
        required = {
            "schema",
            "event_id",
            "occurred_at",
            "provider_area",
            "event_type",
            "case_ref",
            "refs",
            "edges",
            "dependencies",
            "next_action",
            "public_summary",
            "public_safe",
        }
        if set(value) != required:
            raise DomainCaseTimelineError("INVALID_EVENT_FIELDS", "event fields are invalid")
        if value.get("schema") != DOMAIN_CASE_EVENT_SCHEMA:
            raise DomainCaseTimelineError("INVALID_EVENT_SCHEMA", "event schema is invalid")
        if value.get("public_safe") is not True:
            raise DomainCaseTimelineError("EVENT_NOT_PUBLIC_SAFE", "event must be public safe")
        validate_public_safe_payload(value)

        refs = tuple(DomainRef.from_mapping(item) for item in _sequence(value.get("refs"), "refs"))
        edges = tuple(DomainEdge.from_mapping(item) for item in _sequence(value.get("edges"), "edges"))
        dependencies = tuple(
            DomainDependency.from_mapping(item)
            for item in _sequence(value.get("dependencies"), "dependencies")
        )
        if len(refs) > _MAX_REFS_PER_EVENT:
            raise DomainCaseTimelineError("TOO_MANY_REFS", "event has too many refs")
        if len(edges) > _MAX_EDGES_PER_EVENT:
            raise DomainCaseTimelineError("TOO_MANY_EDGES", "event has too many edges")
        if len(dependencies) > _MAX_DEPENDENCIES_PER_EVENT:
            raise DomainCaseTimelineError("TOO_MANY_DEPENDENCIES", "event has too many dependencies")
        case_ref_value = value.get("case_ref")
        return cls(
            event_id=_safe_ref(value.get("event_id"), "event_id"),
            occurred_at=_non_negative_int(value.get("occurred_at"), "occurred_at"),
            provider_area=_enum(value.get("provider_area"), PROVIDER_AREAS, "provider_area"),
            event_type=_safe_text(value.get("event_type"), "event_type"),
            case_ref=None if case_ref_value is None else _safe_ref(case_ref_value, "case_ref"),
            refs=refs,
            edges=edges,
            dependencies=dependencies,
            next_action=DomainNextAction.from_mapping(
                None if value.get("next_action") is None else _mapping(value.get("next_action"), "next_action")
            ),
            public_summary=_safe_text(value.get("public_summary"), "public_summary"),
        )

    def refs_with_case(self) -> tuple[DomainRef, ...]:
        refs = list(self.refs)
        if self.case_ref is not None:
            refs.append(DomainRef(kind="case", ref=self.case_ref))
        return tuple(_dedupe_refs(refs))


def build_case_timeline(events: Iterable[Mapping[str, Any]], *, case_ref: str) -> dict[str, object]:
    """Project public-safe synthetic domain events into one case timeline."""
    wanted_case_ref = _safe_ref(case_ref, "case_ref")
    normalized = _dedupe_events(events)
    graph = _RefGraph()
    for event in normalized:
        refs = event.refs_with_case()
        for ref in refs:
            graph.add(ref)
        if event.case_ref is not None:
            case = DomainRef("case", event.case_ref)
            for ref in refs:
                graph.connect(case, ref, confidence="explicit")
        for edge in event.edges:
            graph.connect(edge.source_ref, edge.target_ref, confidence=edge.confidence)
        for dependency in event.dependencies:
            for ref in refs:
                graph.connect(ref, dependency.dependency_ref, confidence=dependency.confidence)
        if event.next_action.action_ref is not None:
            for ref in refs:
                graph.connect(ref, event.next_action.action_ref, confidence=event.next_action.confidence)

    case_node = DomainRef("case", wanted_case_ref)
    included = [
        event
        for event in normalized
        if any(graph.connected(case_node, ref) for ref in event.refs_with_case())
    ]
    timeline_events = [_timeline_event(event) for event in included]
    refs = _case_refs(included, case_node, graph)
    dependencies = _case_dependencies(included)
    next_action = _case_next_action(included, dependencies)
    result = {
        "schema": CASE_TIMELINE_SCHEMA,
        "case_ref": wanted_case_ref,
        "public_safe": True,
        "authority": _projection_authority(),
        "event_count": len(timeline_events),
        "stable_event_ids": sorted(event.event_id for event in included),
        "refs": refs,
        "timeline": timeline_events,
        "dependencies": dependencies,
        "next_action": next_action,
        "timeline_hash": _stable_hash(
            {
                "case_ref": wanted_case_ref,
                "stable_event_ids": sorted(event.event_id for event in included),
                "refs": refs,
                "dependencies": dependencies,
                "next_action": next_action,
            }
        ),
    }
    validate_public_safe_payload(result)
    return result


def build_dashboard_case_aggregate(events: Iterable[Mapping[str, Any]]) -> dict[str, object]:
    """Return a Dashboard-consumable aggregate without raw provider payloads."""
    normalized = _dedupe_events(events)
    case_refs = sorted(
        {
            ref.ref
            for event in normalized
            for ref in event.refs_with_case()
            if ref.kind == "case"
        }
    )
    cases = []
    for case_ref in case_refs:
        timeline = build_case_timeline((event_to_mapping(event) for event in normalized), case_ref=case_ref)
        provider_counts: dict[str, int] = defaultdict(int)
        for item in timeline["timeline"]:
            provider_counts[str(item["provider_area"])] += 1
        cases.append(
            {
                "case_ref": case_ref,
                "event_count": timeline["event_count"],
                "provider_counts": dict(sorted(provider_counts.items())),
                "dependency_state": _aggregate_dependency_state(timeline["dependencies"]),
                "next_action": timeline["next_action"],
                "timeline_hash": timeline["timeline_hash"],
                "public_safe": True,
            }
        )
    result = {
        "schema": DASHBOARD_CASES_SCHEMA,
        "public_safe": True,
        "authority": _projection_authority(),
        "case_count": len(cases),
        "cases": cases,
    }
    validate_public_safe_payload(result)
    return result


def event_to_mapping(event: DomainCaseEvent) -> dict[str, object]:
    return {
        "schema": DOMAIN_CASE_EVENT_SCHEMA,
        "event_id": event.event_id,
        "occurred_at": event.occurred_at,
        "provider_area": event.provider_area,
        "event_type": event.event_type,
        "case_ref": event.case_ref,
        "refs": [ref.to_mapping() for ref in event.refs],
        "edges": [edge.to_mapping() for edge in event.edges],
        "dependencies": [dependency.to_mapping() for dependency in event.dependencies],
        "next_action": event.next_action.to_mapping(),
        "public_summary": event.public_summary,
        "public_safe": True,
    }


class _RefGraph:
    def __init__(self) -> None:
        self._parent: dict[DomainRef, DomainRef] = {}
        self._uncertain: set[DomainRef] = set()

    def add(self, ref: DomainRef) -> None:
        self._parent.setdefault(ref, ref)

    def connect(self, source: DomainRef, target: DomainRef, *, confidence: str) -> None:
        self.add(source)
        self.add(target)
        if confidence == "uncertain":
            self._uncertain.update({source, target})
            return
        self._parent[self.find(target)] = self.find(source)

    def connected(self, source: DomainRef, target: DomainRef) -> bool:
        if source in self._uncertain or target in self._uncertain:
            return source == target
        if source not in self._parent or target not in self._parent:
            return False
        return self.find(source) == self.find(target)

    def find(self, ref: DomainRef) -> DomainRef:
        parent = self._parent[ref]
        if parent != ref:
            self._parent[ref] = self.find(parent)
        return self._parent[ref]


def _dedupe_events(events: Iterable[Mapping[str, Any]]) -> tuple[DomainCaseEvent, ...]:
    by_id: dict[str, DomainCaseEvent] = {}
    for raw in events:
        event = raw if isinstance(raw, DomainCaseEvent) else DomainCaseEvent.from_mapping(raw)
        if event.event_id in by_id:
            continue
        by_id[event.event_id] = event
        if len(by_id) > _MAX_EVENTS:
            raise DomainCaseTimelineError("TOO_MANY_EVENTS", "too many events")
    return tuple(sorted(by_id.values(), key=lambda item: (item.occurred_at, item.event_id)))


def _timeline_event(event: DomainCaseEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "occurred_at": event.occurred_at,
        "provider_area": event.provider_area,
        "event_type": event.event_type,
        "refs": [ref.to_mapping() for ref in event.refs_with_case()],
        "public_summary": event.public_summary,
    }


def _case_refs(events: Sequence[DomainCaseEvent], case_node: DomainRef, graph: _RefGraph) -> list[dict[str, str]]:
    refs = {case_node}
    for event in events:
        refs.update(event.refs_with_case())
        for edge in event.edges:
            if graph.connected(case_node, edge.source_ref):
                refs.add(edge.target_ref)
            if graph.connected(case_node, edge.target_ref):
                refs.add(edge.source_ref)
        for dependency in event.dependencies:
            refs.add(dependency.dependency_ref)
        if event.next_action.action_ref is not None:
            refs.add(event.next_action.action_ref)
    return [ref.to_mapping() for ref in sorted(refs, key=lambda item: (item.kind, item.ref))]


def _case_dependencies(events: Sequence[DomainCaseEvent]) -> list[dict[str, object]]:
    by_ref: dict[tuple[str, str], DomainDependency] = {}
    rank = {"blocked": 0, "needs_operator": 1, "open": 2, "satisfied": 3}
    for event in events:
        for dependency in event.dependencies:
            key = (dependency.dependency_ref.kind, dependency.dependency_ref.ref)
            current = by_ref.get(key)
            if current is None or rank[dependency.state] < rank[current.state]:
                by_ref[key] = dependency
    return [
        dependency.to_mapping()
        for dependency in sorted(
            by_ref.values(),
            key=lambda item: (item.dependency_ref.kind, item.dependency_ref.ref),
        )
    ]


def _case_next_action(
    events: Sequence[DomainCaseEvent], dependencies: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    uncertain = any(
        event.next_action.confidence == "uncertain"
        or any(edge.confidence == "uncertain" for edge in event.edges)
        or any(dependency.confidence == "uncertain" for dependency in event.dependencies)
        for event in events
    )
    open_dependencies = [
        item
        for item in dependencies
        if item["state"] in {"open", "blocked", "needs_operator"}
    ]
    candidates = [
        event.next_action
        for event in reversed(events)
        if event.next_action.state != "none"
    ]
    if uncertain:
        state = "needs_operator"
        action = None
        reason = "uncertain_edge_requires_operator"
    elif open_dependencies:
        state = "waiting_dependency"
        action = None
        reason = "dependency_not_satisfied"
    elif candidates:
        candidate = candidates[0]
        state = candidate.state
        action = candidate.action_ref
        reason = candidate.reason
    else:
        state = "none"
        action = None
        reason = "no_next_action"
    return {
        "state": state,
        "action_ref": None if action is None else action.to_mapping(),
        "reason": reason,
        "deterministic": True,
        "dashboard_safe": True,
        "side_effect_authority": "not_allowed",
        "external_side_effects_allowed": False,
    }


def _aggregate_dependency_state(dependencies: Sequence[Mapping[str, object]]) -> str:
    states = {str(item["state"]) for item in dependencies}
    if "blocked" in states:
        return "blocked"
    if "needs_operator" in states:
        return "needs_operator"
    if "open" in states:
        return "open"
    return "satisfied" if states else "none"


def _projection_authority() -> dict[str, object]:
    return {
        "authority_classification": "derived_case_timeline_read_model",
        "projection_only": True,
        "canonical_write_allowed": False,
        "scheduler_write_allowed": False,
        "loop_write_allowed": False,
        "external_side_effects_allowed": False,
        "uncertain_edges_can_authorize_side_effects": False,
    }


def _dedupe_refs(refs: Iterable[DomainRef]) -> list[DomainRef]:
    return sorted(set(refs), key=lambda item: (item.kind, item.ref))


def _stable_hash(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainCaseTimelineError(f"INVALID_{field.upper()}", f"{field} must be an object")
    return value


def _sequence(value: object, field: str) -> Sequence[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise DomainCaseTimelineError(f"INVALID_{field.upper()}", f"{field} must be a list")
    return value


def _enum(value: object, allowed: frozenset[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise DomainCaseTimelineError(f"INVALID_{field.upper()}", f"{field} is not allowlisted")
    return value


def _safe_ref(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_REF_RE.fullmatch(value) is None:
        raise DomainCaseTimelineError(f"INVALID_{field.upper()}", f"{field} is invalid")
    return value


def _safe_text(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TEXT_RE.fullmatch(value) is None:
        raise DomainCaseTimelineError(f"INVALID_{field.upper()}", f"{field} is invalid")
    return value


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainCaseTimelineError(f"INVALID_{field.upper()}", f"{field} must be a non-negative integer")
    return value
