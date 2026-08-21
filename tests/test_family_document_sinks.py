from __future__ import annotations

import hashlib

import pytest

from core.family_document_sinks import (
    FamilyDocumentArchiveError,
    FileFamilyDocumentArchive,
    VerifiedMemoryGatewayFamilyDocumentArchive,
)
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.private_memory_stack import PrivateMemoryStack


def record(**overrides):
    value = {
        "schema": "skeleton.family_document_record.v1",
        "record_id": "doc-synthetic",
        "record_hash": "hash",
        "source_sha256": "a" * 64,
    }
    value.update(overrides)
    return value


def test_file_archive_is_idempotent_for_identical_record(tmp_path) -> None:
    archive = FileFamilyDocumentArchive(tmp_path / "archive")

    first = archive.archive(record())
    second = archive.archive(record())

    assert first["archive_state"] == "NEW_ARCHIVE"
    assert second["archive_state"] == "DUPLICATE_IDENTICAL"


def test_file_archive_rejects_conflicting_record_id(tmp_path) -> None:
    archive = FileFamilyDocumentArchive(tmp_path / "archive")
    archive.archive(record())

    with pytest.raises(FamilyDocumentArchiveError):
        archive.archive(record(record_hash="different"))


def _private_gateway(tmp_path) -> tuple[PrivateMemoryStack, MemoryGateway]:
    stack = PrivateMemoryStack(tmp_path / "private-memory")
    stack.init(import_manifest=False)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    return stack, gateway


def test_verified_original_precedes_memorygateway_commit_and_exact_readback(tmp_path) -> None:
    source = tmp_path / "scan.txt"
    source.write_text("synthetic canonical document", encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    value = record(source_sha256=source_hash)
    stack, gateway = _private_gateway(tmp_path)
    original_root = tmp_path / "originals"
    sink = VerifiedMemoryGatewayFamilyDocumentArchive(original_root, gateway)

    receipt = sink.archive(value, source_path=source)

    archived = original_root / "doc-synthetic.txt"
    assert archived.read_bytes() == source.read_bytes()
    assert receipt["original_readback_verified"] is True
    assert receipt["canonical_readback_verified"] is True
    exact = stack.get(namespace="family_document", fact_id="doc-synthetic")
    assert exact["value"]["record_id"] == "doc-synthetic"
    assert exact["value"]["source_sha256"] == source_hash


def test_verified_archive_duplicate_is_idempotent(tmp_path) -> None:
    source = tmp_path / "scan.txt"
    source.write_text("synthetic canonical document", encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    value = record(source_sha256=source_hash)
    _stack, gateway = _private_gateway(tmp_path)
    sink = VerifiedMemoryGatewayFamilyDocumentArchive(tmp_path / "originals", gateway)

    first = sink.archive(value, source_path=source)
    second = sink.archive(value, source_path=source)

    assert first["archive_state"] == "NEW_ARCHIVE"
    assert second["archive_state"] == "DUPLICATE_IDENTICAL"
    assert second["canonical_readback_verified"] is True


def test_verified_archive_rejects_source_changed_before_mutation(tmp_path) -> None:
    source = tmp_path / "scan.txt"
    source.write_text("version two", encoding="utf-8")
    _stack, gateway = _private_gateway(tmp_path)
    sink = VerifiedMemoryGatewayFamilyDocumentArchive(tmp_path / "originals", gateway)

    with pytest.raises(FamilyDocumentArchiveError, match="source changed before archive"):
        sink.archive(record(source_sha256=hashlib.sha256(b"version one").hexdigest()), source_path=source)

    with pytest.raises(Exception):
        _stack.get(namespace="family_document", fact_id="doc-synthetic")


def test_memorygateway_readback_requires_same_canonical_value_hash(tmp_path) -> None:
    source = tmp_path / "scan.txt"
    source.write_text("synthetic canonical document", encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    _stack, gateway = _private_gateway(tmp_path)
    original_execute = gateway.execute

    def corrupt_readback(request):
        response = original_execute(request)
        if request["command"] == "skeleton.memory.private_read_exact":
            payload = dict(response["payload"])
            payload["value_hash"] = "0" * 64
            return {**response, "payload": payload}
        return response

    gateway.execute = corrupt_readback
    sink = VerifiedMemoryGatewayFamilyDocumentArchive(tmp_path / "originals", gateway)

    with pytest.raises(FamilyDocumentArchiveError, match="value hash mismatch"):
        sink.archive(record(source_sha256=source_hash), source_path=source)
