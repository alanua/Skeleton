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
            "route": "DONE",
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
    receipts = rt.outbox.receipts()
    assert receipts[0]["payload"]["receipt_type"] == "package"


def test_telegram_failure_does_not_roll_back_archive_and_marks_retry(tmp_path) -> None:
    rt, inbox = runtime(tmp_path)
    (inbox / "scan.txt").write_text("synthetic document", encoding="utf-8")
    rt.scan_once(drain=False)

    def fail(_message: str) -> None:
        raise RuntimeError("offline")

    result = rt.scan_once(sender=fail)

    assert result["completed_intakes"] == 1
    assert rt.outbox.state_counts() == {"RETRY": 1}
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


def test_outbox_missing_credentials_routes_retry(tmp_path, monkeypatch) -> None:
    outbox = FamilyDocumentReceiptOutbox(tmp_path / "outbox.sqlite")
    outbox.enqueue(
        {
            "schema": "skeleton.family_document_receipt.v1",
            "receipt_key": "package:doc",
            "receipt_type": "package",
            "records": [{"page_count": 1, "classification": {"document_type": "Лист"}}],
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
        "receipt_key": "package:doc",
        "receipt_type": "package",
        "records": [{"page_count": 1}],
        "status": "DONE",
    }
    outbox.enqueue(receipt)

    with pytest.raises(ValueError):
        outbox.enqueue({**receipt, "status": "DIFFERENT"})
