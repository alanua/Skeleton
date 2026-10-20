from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.audit_ledger import AUDIT_EVENT_SCHEMA, AuditLedger
from core.public_receipt import PublicField


def test_audit_ledger_appends_valid_events(tmp_path: Path) -> None:
    ledger_path = tmp_path / "audit.jsonl"
    ledger = AuditLedger(ledger_path)

    ledger.append({"event_type": "operator_note", "project_id": "skeleton", "summary": "safe event"})
    ledger.append({"event_type": "executor_run", "project_id": "skeleton", "status": "ok"})

    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    assert [row["event_type"] for row in rows] == ["operator_note", "executor_run"]
    assert rows[0]["schema"] == AUDIT_EVENT_SCHEMA
    assert rows[0]["created_at"].endswith("Z")


def test_audit_ledger_rejects_secret_looking_fields(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")

    with pytest.raises(ValueError, match="secret field"):
        ledger.append({"event_type": "operator_note", "password": "not-for-ledger"})
    with pytest.raises(ValueError, match="secret material"):
        ledger.append({"event_type": "operator_note", "summary": "API_KEY=not-for-ledger"})

    assert not (tmp_path / "audit.jsonl").exists()


def test_audit_ledger_rejects_private_content_locators(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")

    with pytest.raises(ValueError, match="Drive reference"):
        ledger.append({"event_type": "operator_note", "summary": "https://drive.google.com/file/d/abc/view"})
    with pytest.raises(ValueError, match="raw private path"):
        ledger.append({"event_type": "operator_note", "summary": "/home/agent/private/file.pdf"})
    with pytest.raises(ValueError, match="raw content"):
        ledger.append(
            {
                "event_type": "private_reference_stub",
                "content": "private body must not be stored",
            }
        )


def test_audit_ledger_allows_opaque_private_reference_stub(tmp_path: Path) -> None:
    ledger_path = tmp_path / "audit.jsonl"
    ledger = AuditLedger(ledger_path)

    ledger.append(
        {
            "event_type": "private_reference_stub",
            "stub_id": "private-ref-001",
            "reference_type": "drive_document",
            "label": "operator controlled reference",
        }
    )

    assert json.loads(ledger_path.read_text(encoding="utf-8"))["stub_id"] == "private-ref-001"


def test_audit_ledger_read_recent_works(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    for index in range(5):
        ledger.append({"event_type": "operator_note", "sequence": index})

    assert [event["sequence"] for event in ledger.read_recent(2)] == [3, 4]
    assert ledger.read_recent(0) == []


def test_rotation_preserves_old_file_and_creates_new_file(tmp_path: Path) -> None:
    ledger_path = tmp_path / "audit.jsonl"
    ledger_path.write_text('{"event_type":"old"}\n', encoding="utf-8")
    ledger = AuditLedger(ledger_path)

    ledger.rotate_if_needed(max_size_mb=0)

    rotated = [path for path in tmp_path.iterdir() if path.name.startswith("audit.jsonl.") and path.name.endswith(".rotated")]
    assert len(rotated) == 1
    assert rotated[0].read_text(encoding="utf-8") == '{"event_type":"old"}\n'
    assert ledger_path.exists()
    assert ledger_path.read_text(encoding="utf-8") == ""


def test_audit_ledger_publishes_sanitized_receipt_with_private_evidence_ref(tmp_path: Path) -> None:
    ledger_path = tmp_path / "audit.jsonl"
    ledger = AuditLedger(ledger_path)

    ledger.append_public_receipt(
        event_type="receipt_published",
        receipt={
            "schema": "skeleton.public_receipt.v1",
            "status": "DONE",
            "receipt_id": "receipt-alpha",
            "counts": {"processed": 2},
            "hashes": {"content_hash": "e" * 64},
            "private_payload": {"redacted": True},
        },
        public_fields=(
            PublicField.at("schema", kind="opaque_id"),
            PublicField.at("status", kind="status"),
            PublicField.at("receipt_id", kind="opaque_id"),
            PublicField.at("counts", kind="aggregate"),
            PublicField.at("hashes.content_hash", kind="hash"),
        ),
        private_evidence_ref="private-ref-001",
        metadata={"project_id": "skeleton"},
    )

    row = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert row["private_evidence_ref"] == "private-ref-001"
    assert row["receipt"] == {
        "schema": "skeleton.public_receipt.v1",
        "status": "DONE",
        "receipt_id": "receipt-alpha",
        "counts": {"processed": 2},
        "hashes": {"content_hash": "e" * 64},
    }
    assert "private_payload" not in json.dumps(row, sort_keys=True)
