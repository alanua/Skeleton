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
    ), inbox


def test_no_notification_before_stable_file_gate(tmp_path) -> None:
    rt, inbox = runtime(tmp_path)
    (inbox / "scan.txt").write_text("synthetic document", encoding="utf-8")
    sent = []

    result = rt.scan_once(sender=sent.append)

    assert result["completed_intakes"] == 0
    assert sent == []


def test_success_archives_then_sends_two_done_receipts(tmp_path) -> None:
    rt, inbox = runtime(tmp_path)
    (inbox / "scan.txt").write_text("synthetic document", encoding="utf-8")
    rt.scan_once(drain=False)
    sent = []

    result = rt.scan_once(sender=sent.append)

    assert result["completed_intakes"] == 1
    assert len(sent) == 2
    assert rt.outbox.state_counts() == {"DONE": 2}
    receipts = rt.outbox.receipts()
    assert [item["payload"]["receipt_type"] for item in receipts] == ["intake", "terminal"]


def test_telegram_failure_does_not_roll_back_archive_and_marks_retry(tmp_path) -> None:
    rt, inbox = runtime(tmp_path)
    (inbox / "scan.txt").write_text("synthetic document", encoding="utf-8")
    rt.scan_once(drain=False)

    def fail(_message: str) -> None:
        raise RuntimeError("offline")

    result = rt.scan_once(sender=fail)

    assert result["completed_intakes"] == 1
    assert rt.outbox.state_counts() == {"RETRY": 2}
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
    )
    restarted.source.scan()
    restarted.scan_once(sender=sent.append)

    assert len(sent) == 2
    assert restarted.outbox.state_counts() == {"DONE": 2}


def test_outbox_missing_credentials_routes_retry(tmp_path, monkeypatch) -> None:
    outbox = FamilyDocumentReceiptOutbox(tmp_path / "outbox.sqlite")
    outbox.enqueue(
        {
            "schema": "skeleton.family_document_receipt.v1",
            "receipt_key": "intake:doc",
            "receipt_type": "intake",
            "record_id": "doc",
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
        "receipt_key": "intake:doc",
        "receipt_type": "intake",
        "record_id": "doc",
        "status": "DONE",
    }
    outbox.enqueue(receipt)

    with pytest.raises(ValueError):
        outbox.enqueue({**receipt, "status": "DIFFERENT"})
