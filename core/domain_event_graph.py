from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping


DOMAIN_EVENT_GRAPH_EVENT_SCHEMA = "skeleton.domain_event_graph.event.v1"
DOMAIN_EVENT_GRAPH_RECEIPT_SCHEMA = "skeleton.domain_event_graph.receipt.v1"
DOMAIN_EVENT_GRAPH_QUERY_SCHEMA = "skeleton.domain_event_graph.query.v1"
DOMAIN_EVENT_GRAPH_DEPENDENCY_SCHEMA = "skeleton.domain_event_graph.dependency_state.v1"
DOMAIN_EVENT_GRAPH_FOLLOWUP_SCHEMA = "skeleton.domain_event_graph.followup_tasks.v1"

PUBLIC_SAFE_SCHEMA_ONLY = "PUBLIC_SAFE_SCHEMA_ONLY"
MIN_DESTRUCTIVE_CONFIDENCE = 1.0
VERIFIED_CONFIDENCE = 1.0

DOMAINS = frozenset({"mail", "case", "scheduler", "finance", "gewerbe", "github", "recovery", "documents", "development", "runner"})
ENTITY_KINDS = frozenset({"mail", "case", "schedule", "invoice", "business", "github_check", "recovery", "document", "goal", "runner_task"})
EDGE_KINDS = frozenset(
    {
        "opens_case",
        "schedules",
        "contains_invoice",
        "belongs_to",
        "reports_ci_failure",
        "requires_recovery",
        "filed_as_case",
        "continues_as_runner_task",
        "blocks",
        "depends_on",
    }
)
SAFE_FOLLOWUP_KINDS = frozenset(
    {
        "verify_mail_case_scheduler_bridge",
        "verify_mail_invoice_finance_gewerbe_bridge",
        "verify_github_ci_recovery_bridge",
        "verify_documents_case_bridge",
        "verify_development_runner_bridge",
    }
)

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_HASH_RE = re.compile(r"^[A-Fa-f0-9]{64}$")


class DomainEventGraphError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class EntityRef:
    domain: str
    kind: str
    local_id: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EntityRef":
        if not isinstance(value, Mapping):
            raise DomainEventGraphError("INVALID_ENTITY_REF", "entity ref must be an object")
        return cls(
            domain=_enum(value.get("domain"), DOMAINS, "domain"),
            kind=_enum(value.get("kind"), ENTITY_KINDS, "kind"),
            local_id=_safe_token(value.get("local_id"), "local_id"),
        )

    @classmethod
    def parse(cls, value: object) -> "EntityRef":
        if not isinstance(value, str) or value.count(":") != 2:
            raise DomainEventGraphError("INVALID_ENTITY_REF", "entity ref is malformed")
        domain, kind, local_id = value.split(":", 2)
        return cls(domain=_enum(domain, DOMAINS, "domain"), kind=_enum(kind, ENTITY_KINDS, "kind"), local_id=_safe_token(local_id, "local_id"))

    def stable_id(self) -> str:
        return f"{self.domain}:{self.kind}:{self.local_id}"

    def to_mapping(self) -> dict[str, str]:
        return {"domain": self.domain, "kind": self.kind, "local_id": self.local_id, "ref": self.stable_id()}


@dataclass(frozen=True)
class ProvenanceRef:
    ref: str
    kind: str
    evidence_hash: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProvenanceRef":
        if not isinstance(value, Mapping):
            raise DomainEventGraphError("INVALID_PROVENANCE", "provenance ref must be an object")
        kind = _safe_token(value.get("kind"), "kind")
        evidence_hash = _safe_hash(value.get("evidence_hash"))
        return cls(ref=_safe_ref(value.get("ref")), kind=kind, evidence_hash=evidence_hash)

    def to_mapping(self) -> dict[str, str]:
        return {"ref": self.ref, "kind": self.kind, "evidence_hash": self.evidence_hash}


@dataclass(frozen=True)
class DomainEdge:
    source: EntityRef
    target: EntityRef
    edge_kind: str
    confidence: float
    provenance_refs: tuple[ProvenanceRef, ...]
    inferred: bool = False
    destructive_capable: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DomainEdge":
        if not isinstance(value, Mapping):
            raise DomainEventGraphError("INVALID_EDGE", "edge must be an object")
        provenance = value.get("provenance_refs")
        if not isinstance(provenance, list) or not provenance:
            raise DomainEventGraphError("MISSING_PROVENANCE", "edge requires provenance refs")
        confidence = _confidence(value.get("confidence"))
        inferred = bool(value.get("inferred", False))
        destructive_capable = bool(value.get("destructive_capable", False))
        if destructive_capable and (inferred or confidence < MIN_DESTRUCTIVE_CONFIDENCE):
            raise DomainEventGraphError(
                "UNCERTAIN_LINK_CANNOT_TRIGGER_DESTRUCTIVE_ACTION",
                "destructive-capable edges must be verified with full confidence",
            )
        return cls(
            source=EntityRef.from_mapping(_mapping(value.get("source"), "source")),
            target=EntityRef.from_mapping(_mapping(value.get("target"), "target")),
            edge_kind=_enum(value.get("edge_kind"), EDGE_KINDS, "edge_kind"),
            confidence=confidence,
            provenance_refs=tuple(ProvenanceRef.from_mapping(item) for item in provenance),
            inferred=inferred,
            destructive_capable=destructive_capable,
        )

    def edge_id(self) -> str:
        return "edge_" + stable_hash(
            {
                "source": self.source.stable_id(),
                "target": self.target.stable_id(),
                "edge_kind": self.edge_kind,
                "provenance": [item.to_mapping() for item in self.provenance_refs],
            }
        )[:32]

    def verified(self) -> bool:
        return not self.inferred and self.confidence >= VERIFIED_CONFIDENCE

    def to_mapping(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id(),
            "source": self.source.to_mapping(),
            "target": self.target.to_mapping(),
            "source_ref": self.source.stable_id(),
            "target_ref": self.target.stable_id(),
            "edge_kind": self.edge_kind,
            "confidence": self.confidence,
            "inferred": self.inferred,
            "verified": self.verified(),
            "destructive_capable": self.destructive_capable,
            "provenance_refs": [item.to_mapping() for item in self.provenance_refs],
        }


@dataclass(frozen=True)
class DomainEventEnvelope:
    event_id: str
    event_type: str
    idempotency_key: str
    producer_ref: str
    schema: str
    privacy_boundary: str
    entities: tuple[EntityRef, ...]
    edges: tuple[DomainEdge, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DomainEventEnvelope":
        if not isinstance(value, Mapping) or value.get("schema") != DOMAIN_EVENT_GRAPH_EVENT_SCHEMA:
            raise DomainEventGraphError("INVALID_EVENT_SCHEMA", "event schema is invalid")
        if value.get("privacy_boundary") != PUBLIC_SAFE_SCHEMA_ONLY:
            raise DomainEventGraphError("PRIVACY_BOUNDARY_MISMATCH", "graph events may only carry public-safe refs")
        entities = value.get("entities")
        edges = value.get("edges")
        if not isinstance(entities, list) or not isinstance(edges, list):
            raise DomainEventGraphError("INVALID_EVENT", "entities and edges must be lists")
        envelope = cls(
            event_id=_safe_token(value.get("event_id"), "event_id"),
            event_type=_safe_token(value.get("event_type"), "event_type"),
            idempotency_key=_safe_token(value.get("idempotency_key"), "idempotency_key"),
            producer_ref=_safe_token(value.get("producer_ref"), "producer_ref"),
            schema=DOMAIN_EVENT_GRAPH_EVENT_SCHEMA,
            privacy_boundary=PUBLIC_SAFE_SCHEMA_ONLY,
            entities=tuple(EntityRef.from_mapping(item) for item in entities),
            edges=tuple(DomainEdge.from_mapping(item) for item in edges),
        )
        entity_refs = {entity.stable_id() for entity in envelope.entities}
        for edge in envelope.edges:
            if edge.source.stable_id() not in entity_refs or edge.target.stable_id() not in entity_refs:
                raise DomainEventGraphError("EDGE_ENTITY_NOT_DECLARED", "edge endpoints must be declared entities")
        return envelope

    def payload_hash(self) -> str:
        return stable_hash(
            {
                "schema": self.schema,
                "event_id": self.event_id,
                "event_type": self.event_type,
                "producer_ref": self.producer_ref,
                "privacy_boundary": self.privacy_boundary,
                "entities": [entity.to_mapping() for entity in self.entities],
                "edges": [edge.to_mapping() for edge in self.edges],
            }
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "idempotency_key": self.idempotency_key,
            "producer_ref": self.producer_ref,
            "privacy_boundary": self.privacy_boundary,
            "entities": [entity.to_mapping() for entity in self.entities],
            "edges": [edge.to_mapping() for edge in self.edges],
        }


def bridge_event(
    *,
    event_id: str,
    event_type: str,
    producer_ref: str,
    idempotency_key: str,
    entities: tuple[EntityRef, ...],
    edges: tuple[DomainEdge, ...],
) -> dict[str, object]:
    return DomainEventEnvelope(
        event_id=_safe_token(event_id, "event_id"),
        event_type=_safe_token(event_type, "event_type"),
        producer_ref=_safe_token(producer_ref, "producer_ref"),
        idempotency_key=_safe_token(idempotency_key, "idempotency_key"),
        schema=DOMAIN_EVENT_GRAPH_EVENT_SCHEMA,
        privacy_boundary=PUBLIC_SAFE_SCHEMA_ONLY,
        entities=entities,
        edges=edges,
    ).to_mapping()


def ref(domain: str, kind: str, local_id: str) -> EntityRef:
    return EntityRef(_enum(domain, DOMAINS, "domain"), _enum(kind, ENTITY_KINDS, "kind"), _safe_token(local_id, "local_id"))


def edge(source: EntityRef, target: EntityRef, edge_kind: str, *, evidence_hash: str, evidence_ref: str, confidence: float = 1.0, inferred: bool = False, destructive_capable: bool = False) -> DomainEdge:
    return DomainEdge.from_mapping(
        {
            "source": source.to_mapping(),
            "target": target.to_mapping(),
            "edge_kind": edge_kind,
            "confidence": confidence,
            "inferred": inferred,
            "destructive_capable": destructive_capable,
            "provenance_refs": [{"ref": evidence_ref, "kind": "source_ref", "evidence_hash": evidence_hash}],
        }
    )


def dependency_state(edges: list[Mapping[str, Any]], *, source_ref: str, target_ref: str) -> dict[str, object]:
    matches = [
        edge for edge in edges
        if edge.get("source_ref") == source_ref and edge.get("target_ref") == target_ref and edge.get("edge_kind") in {"blocks", "depends_on"}
    ]
    verified = [edge for edge in matches if edge.get("verified") is True]
    uncertain = [edge for edge in matches if edge.get("verified") is not True]
    state = "SATISFIED" if verified else "BLOCKED_UNVERIFIED" if uncertain else "MISSING"
    return {
        "schema": DOMAIN_EVENT_GRAPH_DEPENDENCY_SCHEMA,
        "source_ref": source_ref,
        "target_ref": target_ref,
        "state": state,
        "verified": bool(verified),
        "destructive_actions_allowed": bool(verified),
        "edge_refs": [str(edge["edge_id"]) for edge in verified],
        "blocked_edge_refs": [str(edge["edge_id"]) for edge in uncertain],
        "public_safe": True,
    }


def bounded_followup_tasks(edges: list[Mapping[str, Any]]) -> dict[str, object]:
    by_kind: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in edges:
        if item.get("verified") is True:
            continue
        kind = _followup_kind(str(item.get("edge_kind", "")))
        if kind is not None:
            by_kind[kind].append(item)
    tasks = []
    for kind in sorted(by_kind):
        edge_refs = sorted(str(edge["edge_id"]) for edge in by_kind[kind])[:10]
        task_id = "followup_" + stable_hash({"kind": kind, "edge_refs": edge_refs})[:24]
        tasks.append(
            {
                "task_id": task_id,
                "kind": kind,
                "title": kind.replace("_", " "),
                "edge_refs": edge_refs,
                "bounded": True,
                "public_safe": True,
                "validation_command": "python3 -m pytest -q tests/test_domain_event_graph.py tests/test_scheduler_domain_dependencies.py",
            }
        )
    return {"schema": DOMAIN_EVENT_GRAPH_FOLLOWUP_SCHEMA, "tasks": tasks, "aggregate_counts": {"record_count": len(tasks)}, "public_safe": True}


def stable_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainEventGraphError("INVALID_EVENT", f"{field} must be an object")
    return value


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN_RE.fullmatch(value):
        raise DomainEventGraphError("INVALID_TOKEN", f"{field} is malformed")
    return value


def _safe_ref(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 160 or any(part in value for part in ("/", "\\", " ", "..")):
        raise DomainEventGraphError("INVALID_REF", "ref is malformed")
    return value


def _safe_hash(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_HASH_RE.fullmatch(value):
        raise DomainEventGraphError("INVALID_HASH", "evidence_hash must be sha256 hex")
    return value.lower()


def _enum(value: object, allowed: frozenset[str], field: str) -> str:
    token = _safe_token(value, field)
    if token not in allowed:
        raise DomainEventGraphError("UNKNOWN_ENUM", f"{field} is not allowlisted")
    return token


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DomainEventGraphError("INVALID_CONFIDENCE", "confidence must be numeric")
    confidence = float(value)
    if confidence < 0.0 or confidence > 1.0:
        raise DomainEventGraphError("INVALID_CONFIDENCE", "confidence must be between 0 and 1")
    return confidence


def _followup_kind(edge_kind: str) -> str | None:
    mapping = {
        "opens_case": "verify_mail_case_scheduler_bridge",
        "contains_invoice": "verify_mail_invoice_finance_gewerbe_bridge",
        "reports_ci_failure": "verify_github_ci_recovery_bridge",
        "filed_as_case": "verify_documents_case_bridge",
        "continues_as_runner_task": "verify_development_runner_bridge",
    }
    kind = mapping.get(edge_kind)
    return kind if kind in SAFE_FOLLOWUP_KINDS else None
