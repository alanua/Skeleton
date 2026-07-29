from __future__ import annotations

import json

import pytest

from core.family_document_sinks import CalendarSink, JsonCommandAdapter, MemoryGatewaySink, SinkError, build_exact_read, build_private_mutation


def record() -> dict[str, object]:
    return {
        "schema": "skeleton.family_document_record.v1", "record_revision": "v1",
        "document_id": "document:" + "a" * 48, "archive": {"sha256": "b" * 64},
        "source": {"source_identity": "source:" + "c" * 48}, "duplicate_relations": [],
        "version_relations": [], "event_candidates": [],
    }


def test_current_private_gateway_envelope_and_exact_read_contract() -> None:
    envelope = build_private_mutation(record(), approval_ref="operator.family.test", source_hash="b" * 64)
    assert envelope["schema"] == "skeleton.memory_gateway.request.v1"
    assert envelope["namespace"] == "skeleton"
    assert envelope["command"] == "skeleton.memory.private_mutate"
    payload = envelope["payload"]
    assert payload["schema"] == "skeleton.private_memory_gateway.mutation.v1"
    assert payload["project_id"] == "skeleton"
    assert payload["dataset_id"] == "family_documents"
    assert payload["fact_namespace"] == "family_documents"
    assert payload["fact_id"].startswith("document:")
    assert payload["approval_ref"] == "operator.family.test"
    read = build_exact_read(payload["fact_namespace"], payload["fact_id"])
    assert read["command"] == "skeleton.memory.private_read_exact"
    assert read["payload"]["canonical_ref"] == f"{payload['fact_namespace']}:{payload['fact_id']}"


def test_mutation_is_deterministic_and_strict_json() -> None:
    first = build_private_mutation(record(), approval_ref="operator.family.test", source_hash="b" * 64)
    second = build_private_mutation(record(), approval_ref="operator.family.test", source_hash="b" * 64)
    assert first == second
    bad = record()
    bad["bad"] = float("nan")
    with pytest.raises(SinkError) as exc:
        build_private_mutation(bad, approval_ref="operator.family.test", source_hash="b" * 64)
    assert exc.value.reason_code == "strict_json_required"


def test_memory_sink_requires_authoritative_exact_read() -> None:
    calls = []
    def runner(command, input_text, timeout, max_output):
        del command, timeout, max_output
        request = json.loads(input_text); calls.append(request)
        if request["command"] == "skeleton.memory.private_mutate":
            return 0, json.dumps({"payload": {"status": "DONE"}}), ""
        return 0, json.dumps({"payload": {"status": "DONE", "authoritative": True, "value": {"document_id": record()["document_id"]}}}), ""
    result = MemoryGatewaySink(JsonCommandAdapter(("/usr/bin/true",), runner=runner), approval_ref="operator.family.test").commit_and_readback(record(), source_hash="b" * 64)
    assert result["status"] == "DONE"
    assert [call["command"] for call in calls] == ["skeleton.memory.private_mutate", "skeleton.memory.private_read_exact"]


def test_memory_sink_rejects_non_authoritative_read() -> None:
    def runner(command, input_text, timeout, max_output):
        del command, timeout, max_output
        request = json.loads(input_text)
        if request["command"] == "skeleton.memory.private_mutate":
            return 0, json.dumps({"payload": {"status": "DEGRADED"}}), ""
        return 0, json.dumps({"payload": {"status": "DONE", "authoritative": False}}), ""
    with pytest.raises(SinkError) as exc:
        MemoryGatewaySink(JsonCommandAdapter(("/usr/bin/true",), runner=runner), approval_ref="operator.family.test").commit_and_readback(record(), source_hash="b" * 64)
    assert exc.value.reason_code == "memory_exact_read_failed"


def test_calendar_sink_is_typed_and_idempotent() -> None:
    seen = []
    def runner(command, input_text, timeout, max_output):
        del command, timeout, max_output
        payload = json.loads(input_text); seen.append(payload)
        return 0, json.dumps({"status": "IDEMPOTENT"}), ""
    sink = CalendarSink(JsonCommandAdapter(("/usr/bin/true",), runner=runner))
    event = {"event_id": "family-document-event:" + "d" * 48, "event_type": "deadline"}
    assert sink.upsert(event) == "IDEMPOTENT"
    assert seen[0]["idempotency_key"] == event["event_id"]
