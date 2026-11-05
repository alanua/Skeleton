from __future__ import annotations

import pytest

from core.family_document_runtime import FamilyDocumentReceiptOutbox, FamilyDocumentRuntime
from core.family_document_sinks import FileFamilyDocumentArchive
from core.family_document_sources import LocalDirectoryDocumentSource


class VerifiedSyntheticArchive(FileFamilyDocumentArchive):
    def archive(self, record, *, source_path=None):
        receipt = super().archive(record, source_path=source_path)
        import json
        import hashlib
        assert json.loads((self.root / f"{record['record_id']}.json").read_text()) == record
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == record["source_sha256"]
        return {**receipt, "original_readback_verified": True,
                "canonical_readback_verified": True, "canonical_ref": "synthetic-canonical"}


def synthetic_classification():
    from tests.test_local_inference_runtime import accepted_output
    value = accepted_output()
    value["document_type"] = "Лист"
    value["evidence"] = {key: ["synthetic document"] for key in value["evidence"]}
    return value


def runtime(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return FamilyDocumentRuntime(
        source=LocalDirectoryDocumentSource(inbox),
        archive_sink=VerifiedSyntheticArchive(tmp_path / "archive"),
        outbox=FamilyDocumentReceiptOutbox(tmp_path / "outbox" / "receipts.sqlite"),
        allowed_subject_aliases=("person-a", "person-b", "person-c"),
        classifier=lambda _text: synthetic_classification(),
    ), inbox


def test_no_notification_before_stable_file_gate(tmp_path) -> None:
    rt, inbox = runtime(tmp_path)
    (inbox / "scan.txt").write_text("synthetic document", encoding="utf-8")
    sent = []

    result = rt.scan_once(sender=sent.append)

    assert result["completed_intakes"] == 0
    assert sent == []


def test_success_archives_then_sends_one_rich_package_report(tmp_path) -> None:
    rt, inbox = runtime(tmp_path)
    (inbox / "scan.txt").write_text("synthetic document", encoding="utf-8")
    rt.scan_once(drain=False)
    sent = []

    result = rt.scan_once(sender=sent.append)

    assert result["completed_intakes"] == 1
    assert len(sent) == 1
    assert "Сканування завершено" in sent[0]
    assert "Лист" in sent[0]
    assert rt.outbox.state_counts() == {"DONE": 1}
    assert rt.outbox.work_state_counts() == {"DONE": 1}
    assert rt.outbox.pending_lifecycle_work() == []
    receipts = rt.outbox.receipts()
    assert receipts[0]["payload"]["receipt_type"] == "package_part"


def test_telegram_failure_does_not_roll_back_archive_or_rerun_document(tmp_path) -> None:
    rt, inbox = runtime(tmp_path)
    (inbox / "scan.txt").write_text("synthetic document", encoding="utf-8")
    rt.scan_once(drain=False)
    rt.scan_once(drain=False)

    def fail(_message: str) -> None:
        raise RuntimeError("offline")

    result = rt.scan_once(sender=fail)

    assert result["completed_intakes"] == 0
    assert result["skipped_completed_or_backoff"] == 1
    assert rt.outbox.state_counts() == {"RETRY": 1}
    assert rt.outbox.work_state_counts() == {"DONE": 1}
    assert len(list((tmp_path / "archive").glob("*.json"))) == 1


def test_replay_restart_does_not_duplicate_successful_sends(tmp_path) -> None:
    rt, inbox = runtime(tmp_path)
    (inbox / "scan.txt").write_text("synthetic document", encoding="utf-8")
    rt.scan_once(drain=False)
    sent = []
    rt.scan_once(sender=sent.append)

    restarted = FamilyDocumentRuntime(
        source=LocalDirectoryDocumentSource(inbox),
        archive_sink=VerifiedSyntheticArchive(tmp_path / "archive"),
        outbox=FamilyDocumentReceiptOutbox(tmp_path / "outbox" / "receipts.sqlite"),
        classifier=rt.classifier,
        allowed_subject_aliases=rt.allowed_subject_aliases,
    )
    restarted.source.scan()
    restarted.scan_once(sender=sent.append)

    assert len(sent) == 1
    assert restarted.outbox.state_counts() == {"DONE": 1}
    assert restarted.outbox.work_state_counts() == {"DONE": 1}
    assert restarted.outbox.pending_lifecycle_work() == []


def test_multipart_partial_failure_retries_only_unsent_part(tmp_path) -> None:
    outbox = FamilyDocumentReceiptOutbox(tmp_path / "outbox.sqlite")
    for index in (1, 2):
        outbox.enqueue(
            {
                "schema": "skeleton.family_document_receipt.v1",
                "receipt_key": f"package:p:{index:04d}",
                "receipt_type": "package_part",
                "package_key": "p",
                "part_index": index,
                "part_count": 2,
                "status": "DONE",
                "message": f"part-{index}",
            }
        )
    calls: list[str] = []

    def flaky(message: str) -> None:
        calls.append(message)
        if message == "part-2" and calls.count("part-2") == 1:
            raise RuntimeError("offline")

    first = outbox.drain(sender=flaky)
    second = outbox.drain(sender=flaky)

    assert first == {"schema": "skeleton.family_document_outbox_drain.v1", "sent": 1, "retry": 1}
    assert second == {"schema": "skeleton.family_document_outbox_drain.v1", "sent": 1, "retry": 0}
    assert calls == ["part-1", "part-2", "part-2"]
    assert outbox.state_counts() == {"DONE": 2}


def test_work_failure_has_bounded_backoff_then_review(tmp_path) -> None:
    outbox = FamilyDocumentReceiptOutbox(tmp_path / "outbox.sqlite")
    for attempt in range(1, 5):
        state = outbox.mark_work_failure("source", "a" * 64, "OcrFailure", now=100)
        assert state == "RETRY"
        assert outbox.should_process("source", "a" * 64, now=100) is False
    state = outbox.mark_work_failure("source", "a" * 64, "OcrFailure", now=100)
    assert state == "REVIEW"
    assert outbox.should_process("source", "a" * 64, now=999999) is False
    assert outbox.work_state_counts() == {"REVIEW": 1}
    pending = outbox.pending_lifecycle_work()
    assert pending[0]["state"] == "BLOCKED"
    assert pending[0]["blocker_reason"] == "OcrFailure"
    assert pending[0]["next_action"] == "operator_review_failed_document_intake"


def test_outbox_missing_credentials_routes_retry(tmp_path, monkeypatch) -> None:
    outbox = FamilyDocumentReceiptOutbox(tmp_path / "outbox.sqlite")
    outbox.enqueue(
        {
            "schema": "skeleton.family_document_receipt.v1",
            "receipt_key": "package:doc:0001",
            "receipt_type": "package_part",
            "message": "synthetic private report",
            "status": "DONE",
        }
    )
    monkeypatch.delenv("SKELETON_TG_BOT", raising=False)
    monkeypatch.delenv("SKELETON_TG_CHAT", raising=False)

    result = outbox.drain()

    assert result["retry"] == 1
    assert outbox.state_counts() == {"RETRY": 1}


def test_outbox_rejects_receipt_key_payload_conflict(tmp_path) -> None:
    outbox = FamilyDocumentReceiptOutbox(tmp_path / "outbox.sqlite")
    receipt = {
        "schema": "skeleton.family_document_receipt.v1",
        "receipt_key": "package:doc:0001",
        "receipt_type": "package_part",
        "message": "one",
        "status": "DONE",
    }
    outbox.enqueue(receipt)

    with pytest.raises(ValueError):
        outbox.enqueue({**receipt, "message": "different"})


@pytest.mark.parametrize("classification", [None, {}, {"summary": " "}, {"route": "ACCEPT", "summary": "invented"}])
def test_invalid_inference_has_one_durable_technical_notice(tmp_path, classification):
    rt, inbox = runtime(tmp_path)
    rt.classifier = lambda _: classification
    (inbox / "scan.txt").write_text("synthetic document")
    rt.scan_once(drain=False)
    rt.scan_once(drain=False)
    # Force retry availability without relying on wall time.
    import sqlite3
    with sqlite3.connect(rt.state.db_path) as db:
        db.execute("UPDATE family_document_work SET next_attempt_at=0")
    rt.scan_once(drain=False)
    state = FamilyDocumentReceiptOutbox(rt.state.db_path)
    receipts = state.receipts()
    assert len(receipts) == 1
    assert receipts[0]["payload"]["receipt_type"] == "technical_failure"
    assert "✅" not in receipts[0]["payload"]["message"]
    assert not list((tmp_path / "archive").glob("*.json"))


def test_missing_archive_readback_never_reports_success(tmp_path):
    rt, inbox = runtime(tmp_path)
    rt.archive_sink = FileFamilyDocumentArchive(tmp_path / "unverified")
    (inbox / "scan.txt").write_text("synthetic document")
    rt.scan_once(drain=False)
    result = rt.scan_once(drain=False)
    assert result["completed_intakes"] == 0
    assert rt.state.receipts()[0]["payload"]["receipt_type"] == "technical_failure"


def test_empty_legacy_outbox_payload_is_not_sent(tmp_path):
    state = FamilyDocumentReceiptOutbox(tmp_path / "state.sqlite")
    state.enqueue({"receipt_key": "legacy-empty", "records": []})
    sent = []
    assert state.drain(sender=sent.append)["retry"] == 1
    assert sent == []


def test_multiple_documents_produce_one_coherent_package(tmp_path):
    rt, inbox = runtime(tmp_path)
    (inbox / "a.txt").write_text("synthetic document a")
    (inbox / "b.txt").write_text("synthetic document b")
    rt.scan_once(drain=False)
    sent = []
    assert rt.scan_once(sender=sent.append)["completed_intakes"] == 2
    assert len(sent) == 1
    assert "Документів: 2" in sent[0]
    assert rt.state.work_state_counts() == {"DONE": 2}