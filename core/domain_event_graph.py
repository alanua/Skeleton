from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Final


DOMAIN_EVENT_ENVELOPE_SCHEMA: Final = "skeleton.domain_event.envelope.v1"
DOMAIN_EVENT_GRAPH_RECEIPT_SCHEMA: Final = "skeleton.domain_event.graph_receipt.v1"
CASE_TIMELINE_SCHEMA: Final = "skeleton.case_timeline.v1"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_ALLOWED_DOMAINS = frozenset(
    {
        "mail",
        "case",
        "scheduler",
        "finance",
        "gewerbe",
        "github",
        "recovery",
        "documents",
        "development",
        "runner",
        "loop",
    }
)
_ALLOWED_REF_TYPES = frozenset(
    {
        "mail",
        "mail_invoice",
        "case",
        "scheduler",
        "finance",
        "gewerbe",
        "github_ci_mail",
        "recovery",
        "document",
        "development_goal",
        "runner_continuation",
        "loop_run",
        "source_event",
    }
)
_BRIDGE_RULES: Final = (
    ("mail", "case", "MAIL_CASE"),
    ("case", "scheduler", "CASE_SCHEDULER"),
    ("case", "finance", "CASE_FINANCE"),
    ("case", "gewerbe", "CASE_GEWERBE"),
    ("mail_invoice", "finance", "MAIL_INVOICE_FINANCE"),
    ("mail_invoice", "gewerbe", "MAIL_INVOICE_GEWERBE"),
    ("github_ci_mail", "recovery", "GITHUB_CI_MAIL_RECOVERY"),
    ("document", "case", "DOCUMENT_CASE"),
    ("document", "finance", "DOCUMENT_FINANCE"),
    ("development_goal", "runner_continuation", "DEVELOPMENT_GOAL_RUNNER_CONTINUATION"),
)


class DomainEventGraphError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class GraphIngestResult:
    event_ref: str
    idempotency_classification: str
    node_refs: tuple[str, ...]
    edge_refs: tuple[str, ...]

    def to_receipt(self) -> dict[str, object]:
        return {
            "schema": DOMAIN_EVENT_GRAPH_RECEIPT_SCHEMA,
            "status": "ACCEPTED",
            "event_ref": self.event_ref,
            "idempotency_classification": self.idempotency_classification,
            "node_refs": list(self.node_refs),
            "edge_refs": list(self.edge_refs),
            "public_safe": True,
            "private_payloads_included": False,
            "canonical_write_performed": False,
        }


class DomainEventGraph:
    """Stable typed graph overlay over existing authority stores.

    The graph stores only public-safe IDs, refs, provenance hashes, confidence and
    relationships. It does not own canonical values and does not persist a new DB.
    """

    def __init__(self) -> None:
        self._events_by_idempotency: dict[str, dict[str, object]] = {}
        self._events_by_ref: dict[str, dict[str, object]] = {}
        self._nodes: dict[str, dict[str, object]] = {}
        self._edges: dict[str, dict[str, object]] = {}
        self._event_edges: dict[str, set[str]] = defaultdict(set)
        self._case_events: dict[str, set[str]] = defaultdict(set)

    def ingest(self, envelope: Mapping[str, Any]) -> dict[str, object]:
        normalized = _normalize_envelope(envelope)
        existing = self._events_by_idempotency.get(normalized["idempotency_key"])
        if existing is not None:
            if existing["event_hash"] != normalized["event_hash"]:
                raise DomainEventGraphError(
                    "IDEMPOTENCY_PAYLOAD_CONFLICT",
                    "idempotency key was reused with a different event envelope",
                )
            return GraphIngestResult(
                event_ref=str(existing["event_ref"]),
                idempotency_classification="DUPLICATE_EXISTING",
                node_refs=tuple(existing["node_refs"]),  # type: ignore[arg-type]
                edge_refs=tuple(existing["edge_refs"]),  # type: ignore[arg-type]
            ).to_receipt()

        event_ref = _stable_ref("event", normalized["idempotency_key"], normalized["event_hash"])
        refs = list(normalized["refs"])
        source_ref = {"ref_type": "source_event", "ref_id": normalized["source_ref"]}
        all_refs = [source_ref, *refs]
        node_refs = tuple(self._ensure_node(ref, normalized) for ref in all_refs)
        edge_refs = tuple(self._ensure_bridge_edges(event_ref, refs, normalized))

        case_refs = [ref["ref_id"] for ref in refs if ref["ref_type"] == "case"]
        for case_ref in case_refs:
            self._case_events[case_ref].add(event_ref)

        event_record = {
            "schema": DOMAIN_EVENT_ENVELOPE_SCHEMA,
            "event_ref": event_ref,
            "event_hash": normalized["event_hash"],
            "idempotency_key": normalized["idempotency_key"],
            "domain": normalized["domain"],
            "event_type": normalized["event_type"],
            "source_ref": normalized["source_ref"],
            "observed_at": normalized["observed_at"],
            "confidence": normalized["confidence"],
            "inferred": normalized["inferred"],
            "provenance_refs": normalized["provenance_refs"],
            "node_refs": node_refs,
            "edge_refs": edge_refs,
            "case_refs": tuple(case_refs),
            "public_safe": True,
            "private_payloads_included": False,
        }
        self._events_by_idempotency[normalized["idempotency_key"]] = event_record
        self._events_by_ref[event_ref] = event_record
        return GraphIngestResult(event_ref, "NEW_EVENT", node_refs, edge_refs).to_receipt()

    def case_timeline(self, *, case_ref: str) -> dict[str, object]:
        case_ref = _safe_token(case_ref, "case_ref")
        rows = []
        blocked_count = 0
        uncertain_count = 0
        for event in self._events_for_case(case_ref):
            state = _event_state(event)
            if state == "waiting_dependency":
                blocked_count += 1
            if state == "inferred_unconfirmed":
                uncertain_count += 1
            rows.append(
                {
                    "event_ref": event["event_ref"],
                    "state": state,
                    "next_operator_action": _next_operator_action(event, state),
                    "domain": event["domain"],
                    "event_type": event["event_type"],
                    "source_ref": event["source_ref"],
                    "observed_at": event["observed_at"],
                    "confidence": event["confidence"],
                    "inferred": event["inferred"],
                    "node_refs": list(event["node_refs"]),  # type: ignore[arg-type]
                    "edge_refs": list(event["edge_refs"]),  # type: ignore[arg-type]
                    "provenance_refs": list(event["provenance_refs"]),  # type: ignore[arg-type]
                }
            )
        rows.sort(key=lambda item: (int(item["observed_at"]), str(item["event_ref"])))
        return {
            "schema": CASE_TIMELINE_SCHEMA,
            "case_ref": case_ref,
            "timeline": rows,
            "aggregate_counts": {
                "event_count": len(rows),
                "node_count": len(self._nodes),
                "edge_count": len(self._edges),
                "blocked_count": blocked_count,
                "missing_provenance_count": uncertain_count,
            },
            "public_safe": True,
            "private_payloads_included": False,
        }

    def authorize_destructive_action(self, *, edge_ref: str) -> bool:
        edge_ref = _safe_token(edge_ref, "edge_ref")
        edge = self._edges.get(edge_ref)
        if edge is None:
            return False
        return (
            edge.get("confidence") == 1.0
            and edge.get("inferred") is False
            and edge.get("authority_classification") == "exact_ref"
        )

    def summary(self) -> dict[str, object]:
        return {
            "schema": "skeleton.domain_event.graph_summary.v1",
            "aggregate_counts": {
                "event_count": len(self._events_by_idempotency),
                "node_count": len(self._nodes),
                "edge_count": len(self._edges),
            },
            "public_safe": True,
            "private_payloads_included": False,
        }

    def record_scheduler_dependency(
        self, *, occurrence_ref: str, dependency_ref: str, observed_at: int, idempotency_key: str
    ) -> dict[str, object]:
        return self.ingest(
            {
                "schema": DOMAIN_EVENT_ENVELOPE_SCHEMA,
                "domain": "scheduler",
                "event_type": "dependency_wait",
                "source_ref": occurrence_ref,
                "observed_at": observed_at,
                "idempotency_key": idempotency_key,
                "refs": [
                    {"ref_type": "scheduler", "ref_id": occurrence_ref},
                    {"ref_type": "scheduler", "ref_id": dependency_ref},
                ],
                "provenance_refs": [{"ref": f"scheduler:{occurrence_ref}", "kind": "scheduler_occurrence"}],
                "confidence": 1.0,
                "inferred": False,
            }
        )

    def record_loop_dependency(
        self, *, run_ref: str, task_ref: str, observed_at: int, idempotency_key: str
    ) -> dict[str, object]:
        return self.ingest(
            {
                "schema": DOMAIN_EVENT_ENVELOPE_SCHEMA,
                "domain": "loop",
                "event_type": "loop_task_dependency",
                "source_ref": run_ref,
                "observed_at": observed_at,
                "idempotency_key": idempotency_key,
                "refs": [
                    {"ref_type": "loop_run", "ref_id": run_ref},
                    {"ref_type": "runner_continuation", "ref_id": task_ref},
                ],
                "provenance_refs": [{"ref": f"loop:{run_ref}", "kind": "loop_state"}],
                "confidence": 1.0,
                "inferred": False,
            }
        )

    def _events_for_case(self, case_ref: str) -> list[dict[str, object]]:
        direct = set(self._case_events.get(case_ref, set()))
        case_node = _node_ref("case", case_ref)
        related_nodes = {case_node}
        for edge_ref, edge in self._edges.items():
            source_ref = edge.get("source_ref")
            target_ref = edge.get("target_ref")
            if source_ref == case_node or target_ref == case_node:
                direct.update(self._event_edges.get(edge_ref, set()))
                if isinstance(source_ref, str):
                    related_nodes.add(source_ref)
                if isinstance(target_ref, str):
                    related_nodes.add(target_ref)
        for event_ref, event in self._events_by_ref.items():
            if related_nodes.intersection(set(event["node_refs"])):  # type: ignore[arg-type]
                direct.add(event_ref)
        return [self._event_by_ref(event_ref) for event_ref in direct]

    def _event_by_ref(self, event_ref: str) -> dict[str, object]:
        event = self._events_by_ref.get(event_ref)
        if event is not None:
            return event
        raise DomainEventGraphError("EVENT_NOT_FOUND", "event ref is not present")

    def _ensure_node(self, ref: Mapping[str, str], event: Mapping[str, object]) -> str:
        node_ref = _node_ref(ref["ref_type"], ref["ref_id"])
        self._nodes.setdefault(
            node_ref,
            {
                "schema": "skeleton.domain_event.node.v1",
                "node_ref": node_ref,
                "ref_type": ref["ref_type"],
                "ref_id": ref["ref_id"],
                "first_seen_event_hash": event["event_hash"],
                "public_safe": True,
                "private_payloads_included": False,
            },
        )
        return node_ref

    def _ensure_bridge_edges(self, event_ref: str, refs: list[dict[str, str]], event: Mapping[str, object]) -> list[str]:
        refs_by_type: dict[str, list[str]] = defaultdict(list)
        for ref in refs:
            refs_by_type[ref["ref_type"]].append(ref["ref_id"])
        edge_refs: list[str] = []
        for source_type, target_type, edge_type in _BRIDGE_RULES:
            for source_id in refs_by_type.get(source_type, []):
                for target_id in refs_by_type.get(target_type, []):
                    edge_ref = self._ensure_edge(
                        source_type=source_type,
                        source_id=source_id,
                        target_type=target_type,
                        target_id=target_id,
                        edge_type=edge_type,
                        event=event,
                    )
                    self._event_edges[edge_ref].add(event_ref)
                    edge_refs.append(edge_ref)
        return edge_refs

    def _ensure_edge(
        self,
        *,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        edge_type: str,
        event: Mapping[str, object],
    ) -> str:
        source_ref = _node_ref(source_type, source_id)
        target_ref = _node_ref(target_type, target_id)
        edge_ref = _stable_ref("edge", edge_type, source_ref, target_ref)
        self._edges.setdefault(
            edge_ref,
            {
                "schema": "skeleton.domain_event.edge.v1",
                "edge_ref": edge_ref,
                "edge_type": edge_type,
                "source_ref": source_ref,
                "target_ref": target_ref,
                "confidence": event["confidence"],
                "inferred": event["inferred"],
                "authority_classification": "inferred_ref" if event["inferred"] else "exact_ref",
                "provenance_refs": event["provenance_refs"],
                "allows_destructive_action": (
                    event["confidence"] == 1.0 and event["inferred"] is False
                ),
                "public_safe": True,
                "private_payloads_included": False,
            },
        )
        return edge_ref


def _event_state(event: Mapping[str, object]) -> str:
    if event.get("confidence") != 1.0 or event.get("inferred") is True:
        return "inferred_unconfirmed"
    if event.get("event_type") == "dependency_wait":
        return "waiting_dependency"
    return "ready"


def _next_operator_action(event: Mapping[str, object], state: str) -> str:
    if state == "inferred_unconfirmed":
        return "confirm_exact_ref_before_side_effect"
    if state == "waiting_dependency":
        return "wait_for_dependency"
    event_type = event.get("event_type")
    if event_type == "mail_case_scheduler_triage":
        return "dispatch_scheduler_followup"
    if event_type in {"document_case_attachment", "document_invoice_case_finance_ref"}:
        return "review_case_finance_ref"
    if event_type == "mail_invoice_routing":
        return "reconcile_invoice_finance_ref"
    if event_type == "scheduler_case_followup_ready":
        return "dispatch_case_followup"
    return "review_timeline"


def synthetic_cross_domain_envelopes() -> tuple[dict[str, object], ...]:
    """Public-safe fixture covering required bridges across more than three domains."""

    base = {
        "schema": DOMAIN_EVENT_ENVELOPE_SCHEMA,
        "observed_at": 100,
        "confidence": 1.0,
        "inferred": False,
        "provenance_refs": [{"ref": "synthetic-source-001", "kind": "synthetic_fixture"}],
    }
    return (
        {
            **base,
            "domain": "mail",
            "event_type": "mail_case_scheduler_triage",
            "source_ref": "mail-001",
            "idempotency_key": "synthetic-mail-case-scheduler-001",
            "refs": [
                {"ref_type": "mail", "ref_id": "mail-001"},
                {"ref_type": "case", "ref_id": "case-001"},
                {"ref_type": "scheduler", "ref_id": "sched-001"},
            ],
        },
        {
            **base,
            "domain": "scheduler",
            "event_type": "scheduler_case_followup_ready",
            "source_ref": "sched-001",
            "idempotency_key": "synthetic-scheduler-case-followup-001",
            "refs": [
                {"ref_type": "scheduler", "ref_id": "sched-001"},
            ],
        },
        {
            **base,
            "domain": "mail",
            "event_type": "mail_invoice_routing",
            "source_ref": "invoice-mail-001",
            "idempotency_key": "synthetic-mail-invoice-finance-gewerbe-001",
            "refs": [
                {"ref_type": "mail_invoice", "ref_id": "invoice-mail-001"},
                {"ref_type": "finance", "ref_id": "finance-case-001"},
                {"ref_type": "gewerbe", "ref_id": "gewerbe-case-001"},
            ],
        },
        {
            **base,
            "domain": "github",
            "event_type": "github_ci_mail_recovery",
            "source_ref": "ci-mail-001",
            "idempotency_key": "synthetic-github-ci-recovery-001",
            "refs": [
                {"ref_type": "github_ci_mail", "ref_id": "ci-mail-001"},
                {"ref_type": "recovery", "ref_id": "recovery-001"},
            ],
        },
        {
            **base,
            "domain": "documents",
            "event_type": "document_invoice_case_finance_ref",
            "source_ref": "doc-001",
            "idempotency_key": "synthetic-document-case-001",
            "refs": [
                {"ref_type": "document", "ref_id": "doc-001"},
                {"ref_type": "case", "ref_id": "case-001"},
                {"ref_type": "finance", "ref_id": "finance-case-001"},
            ],
        },
        {
            **base,
            "domain": "development",
            "event_type": "development_goal_runner_continuation",
            "source_ref": "goal-001",
            "idempotency_key": "synthetic-development-runner-001",
            "refs": [
                {"ref_type": "development_goal", "ref_id": "goal-001"},
                {"ref_type": "runner_continuation", "ref_id": "runner-continue-001"},
            ],
        },
    )


def _normalize_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(envelope, Mapping):
        raise DomainEventGraphError("INVALID_EVENT_ENVELOPE", "event envelope must be an object")
    allowed = {
        "schema",
        "domain",
        "event_type",
        "source_ref",
        "observed_at",
        "idempotency_key",
        "refs",
        "provenance_refs",
        "confidence",
        "inferred",
        "payload_hash",
    }
    if set(envelope) - allowed:
        raise DomainEventGraphError("UNKNOWN_EVENT_FIELD", "event envelope has unknown fields")
    if envelope.get("schema") != DOMAIN_EVENT_ENVELOPE_SCHEMA:
        raise DomainEventGraphError("INVALID_EVENT_SCHEMA", "event envelope schema is invalid")
    domain = _enum(envelope.get("domain"), _ALLOWED_DOMAINS, "domain")
    event_type = _safe_token(envelope.get("event_type"), "event_type")
    source_ref = _safe_token(envelope.get("source_ref"), "source_ref")
    observed_at = _non_negative_int(envelope.get("observed_at"), "observed_at")
    idempotency_key = _safe_token(envelope.get("idempotency_key"), "idempotency_key")
    refs = tuple(_normalize_ref(ref) for ref in _non_empty_list(envelope.get("refs"), "refs"))
    provenance_refs = tuple(_normalize_provenance(ref) for ref in _non_empty_list(envelope.get("provenance_refs"), "provenance_refs"))
    confidence = _confidence(envelope.get("confidence", 1.0))
    inferred = _bool(envelope.get("inferred", False), "inferred")
    payload_hash = envelope.get("payload_hash")
    if payload_hash is not None and (not isinstance(payload_hash, str) or _HASH_RE.fullmatch(payload_hash) is None):
        raise DomainEventGraphError("INVALID_PAYLOAD_HASH", "payload hash must be sha256")
    normalized = {
        "schema": DOMAIN_EVENT_ENVELOPE_SCHEMA,
        "domain": domain,
        "event_type": event_type,
        "source_ref": source_ref,
        "observed_at": observed_at,
        "idempotency_key": idempotency_key,
        "refs": refs,
        "provenance_refs": provenance_refs,
        "confidence": confidence,
        "inferred": inferred,
        "payload_hash": payload_hash,
        "public_safe": True,
        "private_payloads_included": False,
    }
    normalized["event_hash"] = _hash_json(normalized)
    return normalized


def _normalize_ref(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"ref_type", "ref_id"}:
        raise DomainEventGraphError("INVALID_TYPED_REF", "typed ref must have ref_type and ref_id")
    return {
        "ref_type": _enum(value.get("ref_type"), _ALLOWED_REF_TYPES, "ref_type"),
        "ref_id": _safe_token(value.get("ref_id"), "ref_id"),
    }


def _normalize_provenance(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) - {"ref", "kind", "evidence_hash"}:
        raise DomainEventGraphError("INVALID_PROVENANCE", "provenance ref is invalid")
    normalized = {
        "ref": _safe_token(value.get("ref"), "provenance_ref"),
        "kind": _safe_token(value.get("kind"), "provenance_kind"),
    }
    evidence_hash = value.get("evidence_hash")
    if evidence_hash is not None:
        if not isinstance(evidence_hash, str) or _HASH_RE.fullmatch(evidence_hash) is None:
            raise DomainEventGraphError("INVALID_PROVENANCE", "evidence hash is invalid")
        normalized["evidence_hash"] = evidence_hash
    return normalized


def _node_ref(ref_type: str, ref_id: str) -> str:
    return f"{ref_type}:{ref_id}"


def _stable_ref(prefix: str, *parts: object) -> str:
    return f"{prefix}-{_hash_json(parts)[:32]}"


def _hash_json(value: object) -> str:
    encoded = json.dumps(value, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _non_empty_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list) or not value:
        raise DomainEventGraphError("INVALID_EVENT_ENVELOPE", f"{name} must be a non-empty list")
    return value


def _safe_token(value: object, name: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise DomainEventGraphError("INVALID_TOKEN", f"{name} is malformed")
    return value


def _enum(value: object, allowed: Iterable[str], name: str) -> str:
    token = _safe_token(value, name)
    if token not in allowed:
        raise DomainEventGraphError("INVALID_ENUM", f"{name} is not allowed")
    return token


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainEventGraphError("INVALID_INTEGER", f"{name} must be a non-negative integer")
    return value


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DomainEventGraphError("INVALID_CONFIDENCE", "confidence must be numeric")
    confidence = float(value)
    if confidence < 0.0 or confidence > 1.0:
        raise DomainEventGraphError("INVALID_CONFIDENCE", "confidence must be between 0 and 1")
    return confidence


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise DomainEventGraphError("INVALID_BOOLEAN", f"{name} must be boolean")
    return value
