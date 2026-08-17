from __future__ import annotations

import pytest

from core.family_document_runtime import FamilyDocumentReceiptOutbox, FamilyDocumentRuntime
from core.family_document_sinks import FileFamilyDocumentArchive
from core.family_document_sources import LocalDirectoryDocumentSource


def runtime(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return FamilyDocumentRuntime(
        source=LocalDirectoryDocumentSource(inbox),
        archive_sink=FileFamilyDocumentArchive(tmp_path / "archive"),
        outbox=FamilyDocumentReceiptOutbox(tmp_path / "outbox" / "receipts.sqlite"),
        classifier=lambda _text: {
            "document_type": "Лист",
            "topic_alias": "Документи",
            "summary": "Синтетичний документ для тесту.",
            "confidence": 0.99,
            "route": "ACCEPT",
        },
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
        archive_sink=FileFamilyDocumentArchive(tmp_path / "archive"),
        outbox=FamilyDocumentReceiptOutbox(tmp_path / "outbox" / "receipts.sqlite"),
        classifier=rt.classifier,
    )
    restarted.source.scan()
    restarted.scan_once(sender=sent.append)

    assert len(sent) == 1
    assert restarted.outbox.state_counts() == {"DONE": 1}
    assert restarted.outbox.work_state_counts() == {"DONE": 1}


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
