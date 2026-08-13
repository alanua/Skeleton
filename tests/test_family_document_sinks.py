from __future__ import annotations

import pytest

from core.family_document_sinks import FamilyDocumentSinkError, VerifiedArchive, aggregate_receipt, private_put_request


def test_verified_archive_is_idempotent_and_readback_checked(tmp_path) -> None:
    archive = VerifiedArchive(tmp_path)
    first = archive.write_record_once("record.json", {"schema": "x", "value": 1})
    second = archive.write_record_once("record.json", {"schema": "x", "value": 1})
    assert first.sha256 == second.sha256
    with pytest.raises(FamilyDocumentSinkError):
        archive.write_record_once("record.json", {"schema": "x", "value": 2})


def test_private_put_request_targets_gateway_interface_only() -> None:
    request = private_put_request(
        fact_namespace="family_document",
        fact_id="sha256-a",
        value={"schema": "skeleton.family_document_record.v1"},
        source_hash="0" * 64,
        idempotency_key="family-document-" + "0" * 64,
        approval_ref="synthetic",
    )
    assert request["command"] == "skeleton.memory.private_mutate"
    assert request["payload"]["operation"] == "put"  # type: ignore[index]


def test_public_receipt_is_aggregate_only() -> None:
    receipt = aggregate_receipt(status="DONE", duplicate=False, event_count=2)
    assert receipt["privacy"] == "aggregate_only"
    assert "path" not in receipt
