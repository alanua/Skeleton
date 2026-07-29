from __future__ import annotations
import json
import pytest
from core.family_document_sinks import CalendarSink, JsonCommandAdapter, MemoryGatewaySink, SinkError, build_exact_read, build_private_mutation

def record(): return {"schema":"skeleton.family_document_record.v1","record_revision":"v1","document_id":"document:"+"a"*48,"archive":{"sha256":"b"*64},"source":{"source_identity":"source:"+"c"*48},"duplicate_relations":[],"version_relations":[],"event_candidates":[]}
def test_current_private_gateway_envelope_and_exact_read_contract():
    envelope=build_private_mutation(record(),approval_ref="operator.family.test",source_hash="b"*64); payload=envelope["payload"]; assert envelope["schema"]=="skeleton.memory_gateway.request.v1" and envelope["command"]=="skeleton.memory.private_mutate" and payload["schema"]=="skeleton.private_memory_gateway.mutation.v1" and payload["dataset_id"]=="family_documents"; read=build_exact_read(payload["fact_namespace"],payload["fact_id"]); assert read["command"]=="skeleton.memory.private_read_exact"
def test_mutation_is_deterministic_and_strict_json():
    assert build_private_mutation(record(),approval_ref="operator.family.test",source_hash="b"*64)==build_private_mutation(record(),approval_ref="operator.family.test",source_hash="b"*64); bad=record(); bad["bad"]=float("nan")
    with pytest.raises(SinkError): build_private_mutation(bad,approval_ref="operator.family.test",source_hash="b"*64)
def test_memory_sink_requires_authoritative_exact_read():
    calls=[]
    def runner(command,input_text,timeout,max_output):
        del command,timeout,max_output; request=json.loads(input_text); calls.append(request)
        if request["command"]=="skeleton.memory.private_mutate": return 0,json.dumps({"payload":{"status":"DONE"}}),""
        return 0,json.dumps({"payload":{"status":"DONE","authoritative":True,"value":{"document_id":record()["document_id"]}}}),""
    result=MemoryGatewaySink(JsonCommandAdapter(("/usr/bin/true",),runner=runner),approval_ref="operator.family.test").commit_and_readback(record(),source_hash="b"*64); assert result["status"]=="DONE" and [c["command"] for c in calls]==["skeleton.memory.private_mutate","skeleton.memory.private_read_exact"]
def test_memory_sink_rejects_non_authoritative_read():
    def runner(command,input_text,timeout,max_output):
        del command,timeout,max_output; request=json.loads(input_text); return (0,json.dumps({"payload":{"status":"DEGRADED"}}),"") if request["command"]=="skeleton.memory.private_mutate" else (0,json.dumps({"payload":{"status":"DONE","authoritative":False}}),"")
    with pytest.raises(SinkError) as exc: MemoryGatewaySink(JsonCommandAdapter(("/usr/bin/true",),runner=runner),approval_ref="operator.family.test").commit_and_readback(record(),source_hash="b"*64)
    assert exc.value.reason_code=="memory_exact_read_failed"
def test_memory_sink_rejects_authoritative_read_without_private_value():
    def runner(command,input_text,timeout,max_output):
        del command,timeout,max_output; request=json.loads(input_text); return (0,json.dumps({"payload":{"status":"DONE"}}),"") if request["command"]=="skeleton.memory.private_mutate" else (0,json.dumps({"payload":{"status":"DONE","authoritative":True}}),"")
    with pytest.raises(SinkError) as exc: MemoryGatewaySink(JsonCommandAdapter(("/usr/bin/true",),runner=runner),approval_ref="operator.family.test").commit_and_readback(record(),source_hash="b"*64)
    assert exc.value.reason_code=="memory_exact_read_value_missing"
def test_calendar_sink_is_typed_and_idempotent():
    seen=[]
    def runner(command,input_text,timeout,max_output): del command,timeout,max_output; seen.append(json.loads(input_text)); return 0,json.dumps({"status":"IDEMPOTENT"}),""
    event={"event_id":"family-document-event:"+"d"*48,"event_type":"deadline"}; assert CalendarSink(JsonCommandAdapter(("/usr/bin/true",),runner=runner)).upsert(event)=="IDEMPOTENT" and seen[0]["idempotency_key"]==event["event_id"]
