from __future__ import annotations

import pytest

from core.domain_event_graph import (
    DOMAIN_EVENT_ENVELOPE_SCHEMA,
    DomainEventGraph,
    DomainEventGraphError,
    synthetic_cross_domain_envelopes,
)
from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_policy import MemoryGatewayPolicyError


def _request(suffix: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
        "namespace": "skeleton",
        "command": f"skeleton.{suffix}",
        "payload": payload,
    }


def test_cross_domain_source_is_traceable_through_case_timeline_and_replay_is_idempotent() -> None:
    graph = DomainEventGraph()
    envelopes = synthetic_cross_domain_envelopes()

    first = graph.ingest(envelopes[0])
    duplicate = graph.ingest(envelopes[0])
    for envelope in envelopes[1:]:
        graph.ingest(envelope)

    timeline = graph.case_timeline(case_ref="case-001")

    assert first["idempotency_classification"] == "NEW_EVENT"
    assert duplicate["idempotency_classification"] == "DUPLICATE_EXISTING"
    assert first["event_ref"] == duplicate["event_ref"]
    assert timeline["aggregate_counts"]["event_count"] == 4
    domains = {row["domain"] for row in timeline["timeline"]}
    assert domains == {"mail", "documents", "scheduler"}
    event_types = {row["event_type"] for row in timeline["timeline"]}
    assert {
        "mail_case_scheduler_triage",
        "scheduler_case_followup_ready",
        "mail_invoice_routing",
        "document_invoice_case_finance_ref",
    } <= event_types
    actions = {row["event_type"]: row["next_operator_action"] for row in timeline["timeline"]}
    assert actions["mail_case_scheduler_triage"] == "dispatch_scheduler_followup"
    assert actions["scheduler_case_followup_ready"] == "dispatch_case_followup"
    assert actions["mail_invoice_routing"] == "reconcile_invoice_finance_ref"
    assert actions["document_invoice_case_finance_ref"] == "review_case_finance_ref"
    assert {row["state"] for row in timeline["timeline"]} == {"ready"}
    edge_refs = {edge for row in timeline["timeline"] for edge in row["edge_refs"]}
    assert len(edge_refs) >= 5
    assert timeline["private_payloads_included"] is False


def test_reusing_idempotency_key_with_different_payload_fails() -> None:
    graph = DomainEventGraph()
    envelope = dict(synthetic_cross_domain_envelopes()[0])
    graph.ingest(envelope)
    changed = dict(envelope)
    changed["observed_at"] = 101

    with pytest.raises(DomainEventGraphError) as excinfo:
        graph.ingest(changed)

    assert excinfo.value.reason_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"


def test_uncertain_inferred_link_cannot_authorize_destructive_action() -> None:
    graph = DomainEventGraph()
    envelope = {
        "schema": DOMAIN_EVENT_ENVELOPE_SCHEMA,
        "domain": "documents",
        "event_type": "document_case_attachment",
        "source_ref": "doc-uncertain",
        "observed_at": 100,
        "idempotency_key": "uncertain-doc-case",
        "refs": [
            {"ref_type": "document", "ref_id": "doc-uncertain"},
            {"ref_type": "case", "ref_id": "case-uncertain"},
        ],
        "provenance_refs": [{"ref": "synthetic-source-uncertain", "kind": "synthetic_fixture"}],
        "confidence": 0.62,
        "inferred": True,
    }

    receipt = graph.ingest(envelope)

    assert receipt["edge_refs"]
    assert graph.authorize_destructive_action(edge_ref=receipt["edge_refs"][0]) is False
    timeline = graph.case_timeline(case_ref="case-uncertain")
    assert timeline["aggregate_counts"]["blocked_count"] == 0
    assert timeline["aggregate_counts"]["missing_provenance_count"] == 1
    assert timeline["timeline"][0]["state"] == "inferred_unconfirmed"
    assert timeline["timeline"][0]["next_operator_action"] == "confirm_exact_ref_before_side_effect"


def test_scheduler_dependency_wait_adds_deterministic_blocked_case_state() -> None:
    graph = DomainEventGraph()
    for envelope in synthetic_cross_domain_envelopes():
        graph.ingest(envelope)
    graph.record_scheduler_dependency(
        occurrence_ref="sched-001",
        dependency_ref="sched-dependency-001",
        observed_at=120,
        idempotency_key="synthetic-scheduler-wait-case-001",
    )

    timeline = graph.case_timeline(case_ref="case-001")
    waiting = [row for row in timeline["timeline"] if row["event_type"] == "dependency_wait"]

    assert len(waiting) == 1
    assert waiting[0]["state"] == "waiting_dependency"
    assert waiting[0]["next_operator_action"] == "wait_for_dependency"
    assert timeline["aggregate_counts"]["blocked_count"] == 1


def test_memory_gateway_exposes_public_safe_case_timeline() -> None:
    graph = DomainEventGraph()
    gw = MemoryGateway(capability_token(namespaces=("skeleton",)), domain_event_graph=graph)
    envelope = synthetic_cross_domain_envelopes()[0]

    ingested = gw.execute(_request("graph.ingest_domain_event", {"envelope": envelope}))
    timeline = gw.execute(_request("graph.get_case_timeline", {"case_ref": "case-001"}))

    assert ingested["payload"]["idempotency_classification"] == "NEW_EVENT"
    assert timeline["payload"]["schema"] == "skeleton.case_timeline.v1"
    assert timeline["payload"]["aggregate_counts"]["event_count"] == 1
    assert timeline["payload"]["public_safe"] is True
    assert "payload_hash" not in timeline["payload"]


def test_gateway_requires_explicit_graph_injection() -> None:
    gw = MemoryGateway(capability_token(namespaces=("skeleton",)))

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.execute(_request("graph.get_case_timeline", {"case_ref": "case-001"}))

    assert excinfo.value.reason_code == "DOMAIN_EVENT_GRAPH_REQUIRED"
