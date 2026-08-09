from __future__ import annotations

from pathlib import Path

import pytest

from core.domain_event_graph import (
    DOMAIN_EVENT_GRAPH_EVENT_SCHEMA,
    PUBLIC_SAFE_SCHEMA_ONLY,
    DomainEventGraphError,
    bridge_event,
    edge,
    ref,
)
from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_policy import MemoryGatewayPolicyError
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack


def _request(suffix: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
        "namespace": "skeleton",
        "command": f"skeleton.{suffix}",
        "payload": payload,
    }


def _gateway(tmp_path: Path) -> MemoryGateway:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    return MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )


def _event(event_id: str, *edges) -> dict[str, object]:
    entities = []
    seen = set()
    for item in edges:
        for entity in (item.source, item.target):
            if entity.stable_id() not in seen:
                entities.append(entity)
                seen.add(entity.stable_id())
    return bridge_event(
        event_id=event_id,
        event_type="bridge_verified",
        producer_ref="synthetic_bridge_tests",
        idempotency_key=f"idem_{event_id}",
        entities=tuple(entities),
        edges=tuple(edges),
    )


def test_domain_graph_event_is_idempotent_and_public_safe(tmp_path: Path) -> None:
    mail = ref("mail", "mail", "msg_001")
    case = ref("case", "case", "case_001")
    schedule = ref("scheduler", "schedule", "sched_001")
    event = _event(
        "mail_case_scheduler",
        edge(mail, case, "opens_case", evidence_hash="a" * 64, evidence_ref="mail:msg_001"),
        edge(case, schedule, "schedules", evidence_hash="b" * 64, evidence_ref="case:case_001"),
    )
    gw = _gateway(tmp_path)

    first = gw.execute(_request("domain_graph.apply_event", event))["payload"]
    second = gw.execute(_request("domain_graph.apply_event", event))["payload"]
    query = gw.execute(_request("domain_graph.query_edges", {"project_id": "skeleton"}))["payload"]

    assert first["idempotency_classification"] == "NEW_MUTATION"
    assert second["idempotency_classification"] == "DUPLICATE_IDENTICAL"
    assert first["node_count"] == 3
    assert first["edge_count"] == 2
    assert first["private_payloads_included"] is False
    assert len(query["results"]) == 2
    assert event["privacy_boundary"] == PUBLIC_SAFE_SCHEMA_ONLY
    assert "invoice total" not in str(query).lower()


def test_domain_graph_requires_provenance_and_blocks_uncertain_destructive_edges() -> None:
    source = ref("mail", "mail", "msg_002")
    target = ref("finance", "invoice", "invoice_002")
    with pytest.raises(DomainEventGraphError) as excinfo:
        edge(
            source,
            target,
            "contains_invoice",
            evidence_hash="c" * 64,
            evidence_ref="mail:msg_002",
            confidence=0.62,
            inferred=True,
            destructive_capable=True,
        )

    assert excinfo.value.reason_code == "UNCERTAIN_LINK_CANNOT_TRIGGER_DESTRUCTIVE_ACTION"


def test_domain_graph_rejects_private_payload_shape(tmp_path: Path) -> None:
    gw = _gateway(tmp_path)
    event = {
        "schema": DOMAIN_EVENT_GRAPH_EVENT_SCHEMA,
        "privacy_boundary": PUBLIC_SAFE_SCHEMA_ONLY,
        "event_id": "bad_payload",
        "event_type": "bridge_verified",
        "producer_ref": "synthetic_bridge_tests",
        "idempotency_key": "idem_bad_payload",
        "entities": [{"domain": "mail", "kind": "mail", "local_id": "msg with spaces"}],
        "edges": [],
    }

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.execute(_request("domain_graph.apply_event", event))

    assert excinfo.value.reason_code == "DomainEventGraphError"


def test_requested_cross_domain_bridges_and_followups(tmp_path: Path) -> None:
    gw = _gateway(tmp_path)
    bridge_edges = [
        edge(ref("mail", "mail", "msg_101"), ref("case", "case", "case_101"), "opens_case", evidence_hash="1" * 64, evidence_ref="mail:msg_101"),
        edge(ref("case", "case", "case_101"), ref("scheduler", "schedule", "sched_101"), "schedules", evidence_hash="2" * 64, evidence_ref="case:case_101"),
        edge(ref("mail", "mail", "msg_102"), ref("finance", "invoice", "invoice_102"), "contains_invoice", evidence_hash="3" * 64, evidence_ref="mail:msg_102", confidence=0.7, inferred=True),
        edge(ref("finance", "invoice", "invoice_102"), ref("gewerbe", "business", "gewerbe_102"), "belongs_to", evidence_hash="4" * 64, evidence_ref="finance:invoice_102"),
        edge(ref("github", "github_check", "ci_103"), ref("recovery", "recovery", "recovery_103"), "requires_recovery", evidence_hash="5" * 64, evidence_ref="github:ci_103"),
        edge(ref("mail", "mail", "msg_103"), ref("github", "github_check", "ci_103"), "reports_ci_failure", evidence_hash="6" * 64, evidence_ref="mail:msg_103"),
        edge(ref("documents", "document", "doc_104"), ref("case", "case", "case_104"), "filed_as_case", evidence_hash="7" * 64, evidence_ref="documents:doc_104"),
        edge(ref("development", "goal", "goal_105"), ref("runner", "runner_task", "runner_105"), "continues_as_runner_task", evidence_hash="8" * 64, evidence_ref="development:goal_105", confidence=0.5, inferred=True),
    ]

    gw.execute(_request("domain_graph.apply_event", _event("requested_bridges", *bridge_edges)))
    followups = gw.execute(_request("domain_graph.followup_tasks", {"project_id": "skeleton"}))["payload"]
    invoice_edges = gw.execute(
        _request("domain_graph.query_edges", {"source_ref": "mail:mail:msg_102", "edge_kind": "contains_invoice"})
    )["payload"]["results"]

    assert invoice_edges[0]["verified"] is False
    assert {task["kind"] for task in followups["tasks"]} == {
        "verify_mail_invoice_finance_gewerbe_bridge",
        "verify_development_runner_bridge",
    }
