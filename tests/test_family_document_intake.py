from __future__ import annotations

import json
from pathlib import Path

from core.family_document_intake import DocumentProcessor, IntakeConfig, Person, stable_hash
from core.family_document_runtime import ProjectionOutbox
from core.family_document_sinks import CalendarSink, JsonCommandAdapter, MemoryGatewaySink
from core.family_document_sources import ApprovedRoot
from core.local_document_ocr import OcrResult


class FakeOcr:
    def __init__(self, text_by_name: dict[str, str]) -> None: self.text_by_name = text_by_name
    def extract(self, source: Path) -> OcrResult:
        text = self.text_by_name[source.name]; digest = __import__("hashlib").sha256(source.read_bytes()).hexdigest(); text_hash = __import__("hashlib").sha256(text.encode()).hexdigest(); return OcrResult(text, text, ("synthetic",), digest, text_hash, text_hash)


def make_processor(tmp_path: Path, text_by_name: dict[str, str], *, calendar_fail: bool = False):
    inbox, archive = tmp_path / "inbox", tmp_path / "archive"; inbox.mkdir(exist_ok=True); archive.mkdir(exist_ok=True); calls = []
    def memory_runner(command, input_text, timeout, max_output):
        del command, timeout, max_output
        request = json.loads(input_text); calls.append(request)
        if request["command"] == "skeleton.memory.private_mutate": return 0, json.dumps({"payload": {"status": "DEGRADED"}}), ""
        document_id = calls[-2]["payload"]["value"]["document_id"]
        return 0, json.dumps({"payload": {"status": "DONE", "authoritative": True, "value": {"document_id": document_id}}}), ""
    calendar_calls = []
    def calendar_runner(command, input_text, timeout, max_output):
        del command, timeout, max_output
        request = json.loads(input_text); calendar_calls.append(request)
        return (1, "", "") if calendar_fail else (0, json.dumps({"status": "IDEMPOTENT"}), "")
    processor = DocumentProcessor(IntakeConfig(people=(Person("oleksii", ("Oleksii Antonienkov", "Олексій Антонієнков")), Person("spouse", ("Synthetic Spouse",)), Person("son", ("Synthetic Son",))), approved_roots=(ApprovedRoot("mfp", inbox),), archive_root=archive, memory_sink=MemoryGatewaySink(JsonCommandAdapter(("/usr/bin/true",), runner=memory_runner), approval_ref="operator.family.test"), calendar_sink=CalendarSink(JsonCommandAdapter(("/usr/bin/true",), runner=calendar_runner)), projection_outbox=ProjectionOutbox(tmp_path / "outbox.json"), ocr=FakeOcr(text_by_name)))
    return processor, inbox, archive, calls, calendar_calls


def ready_text() -> str: return "Issuer: Jobcenter Brandenburg\nOleksii Antonienkov\nBescheid Bürgergeld Deutschland\nAktenzeichen: JC-2026-1234\nBetrag 325,10 EUR\nFrist 15.08.2026\nDokumentdatum 25.07.2026\n"


def test_complete_private_record_and_semantic_event_date(tmp_path: Path) -> None:
    processor, inbox, _, _, _ = make_processor(tmp_path, {"scan.pdf": ready_text()}); source = inbox / "scan.pdf"; source.write_bytes(b"binary"); plan = processor.plan(source); record = plan.record
    assert plan.ready is True and record["principal_subject"] == "oleksii" and record["all_subjects"] == ["oleksii"]
    assert record["topic"] == "02 migration_and_residence" and record["jurisdiction_country"] == "DE" and record["document_type"] == "official notice" and record["issuer"] == "Jobcenter Brandenburg"
    assert record["identifiers"][0]["value"] == "JC-2026-1234" and record["amounts"][0]["currency"] == "EUR" and record["deadlines"][0]["date"] == "2026-08-15"
    assert plan.calendar_events[0]["date"] == "2026-08-15" and plan.calendar_events[0]["event_type"] == "deadline" and record["source"]["absolute_path"] == str(source.resolve()) and record["ocr"]["providers"] == ["synthetic"]


def test_archive_readback_precedes_memory_and_duplicate_replay_is_idempotent(tmp_path: Path) -> None:
    processor, inbox, archive, calls, calendar_calls = make_processor(tmp_path, {"scan.pdf": ready_text()}); source = inbox / "scan.pdf"; source.write_bytes(b"binary")
    first = processor.process(source); assert first["status"] == "DONE" and first["counts"]["written"] == 1 and first["counts"]["canonical_readbacks"] == 1 and calls[0]["payload"]["value"]["archive"]["readback_verified"] is True
    assert len([path for path in archive.rglob("*") if path.is_file()]) == 1; first_event_id = calendar_calls[0]["event"]["event_id"]
    second = processor.process(source); assert second["counts"]["written"] == 0 and second["counts"]["duplicates"] == 1 and len([path for path in archive.rglob("*") if path.is_file()]) == 1
    assert calls[0]["payload"]["idempotency_key"] == calls[2]["payload"]["idempotency_key"] and calendar_calls[1]["event"]["event_id"] == first_event_id
    assert calls[2]["payload"]["value"]["duplicate_relations"] == [calls[2]["payload"]["value"]["source"]["source_identity"]]


def test_projection_or_calendar_degradation_does_not_rollback_canonical_commit(tmp_path: Path) -> None:
    processor, inbox, _, calls, _ = make_processor(tmp_path, {"scan.pdf": ready_text()}, calendar_fail=True); source = inbox / "scan.pdf"; source.write_bytes(b"binary"); receipt = processor.process(source)
    assert receipt["status"] == "DONE" and receipt["reason_code"] == "calendar_degraded" and receipt["counts"]["canonical_commits"] == 1 and receipt["counts"]["calendar_failed"] == 1
    assert [calls[0]["command"], calls[1]["command"]] == ["skeleton.memory.private_mutate", "skeleton.memory.private_read_exact"] and processor.config.projection_outbox.counts() == {"PENDING": 1}


def test_ambiguous_subject_routes_to_review_without_side_effects(tmp_path: Path) -> None:
    processor, inbox, archive, calls, calendar_calls = make_processor(tmp_path, {"scan.pdf": ready_text() + "\nSynthetic Spouse\n"}); source = inbox / "scan.pdf"; source.write_bytes(b"binary"); receipt = processor.process(source)
    assert receipt["status"] == "REVIEW" and receipt["reason_code"] == "review_required" and list(archive.rglob("*")) == [] and calls == [] and calendar_calls == []


def test_reconciliation_packet_is_deterministic_zero_side_effect_and_clusters(tmp_path: Path) -> None:
    text = ready_text(); processor, inbox, archive, calls, calendar_calls = make_processor(tmp_path, {"a.pdf": text, "b.pdf": text, "c.pdf": text.replace("25.07.2026", "26.07.2026")}); (inbox / "a.pdf").write_bytes(b"same"); (inbox / "b.pdf").write_bytes(b"same"); (inbox / "c.pdf").write_bytes(b"different")
    first_packet, first_receipt = processor.reconcile(); second_packet, second_receipt = processor.reconcile(); assert first_packet == second_packet and first_receipt == second_receipt and first_packet["zero_side_effect"] is True
    assert first_packet["packet_hash"] == stable_hash({"schema": first_packet["schema"], "zero_side_effect": True, "items": first_packet["items"]}) and first_receipt["counts"]["duplicate_groups"] == 1 and first_receipt["counts"]["version_groups"] == 1
    duplicate_items = [item for item in first_packet["items"] if item["duplicate_relations"]]; assert duplicate_items and all(value.startswith("source:") and value != item["document_id"] for item in duplicate_items for value in item["duplicate_relations"])
    assert list(archive.rglob("*")) == [] and calls == [] and calendar_calls == []


def test_public_receipts_do_not_expose_private_values(tmp_path: Path) -> None:
    processor, inbox, _, _, _ = make_processor(tmp_path, {"scan.pdf": ready_text()}); source = inbox / "scan.pdf"; source.write_bytes(b"binary"); rendered = json.dumps(processor.process(source))
    assert str(tmp_path) not in rendered and "Oleksii" not in rendered and "Jobcenter" not in rendered and "JC-2026-1234" not in rendered
