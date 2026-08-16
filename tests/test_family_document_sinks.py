from __future__ import annotations

import hashlib

import pytest

from core.family_document_sinks import (
    CompositeFamilyDocumentArchive,
    FamilyDocumentArchiveError,
    FileFamilyDocumentArchive,
    MemoryGatewayFamilyDocumentArchive,
)


def record(**overrides):
    value = {
        "schema": "skeleton.family_document_record.v1",
        "record_id": "doc-synthetic",
        "record_hash": "hash",
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


def test_original_binary_is_hash_verified_before_archive_success(tmp_path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"synthetic-pdf")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    archive = FileFamilyDocumentArchive(tmp_path / "archive")

    receipt = archive.archive(record(source_sha256=source_hash), source_path=source)

    assert receipt["original_sha256"] == source_hash
    originals = list((tmp_path / "archive" / "originals").glob("*.pdf"))
    assert len(originals) == 1
    assert hashlib.sha256(originals[0].read_bytes()).hexdigest() == source_hash


def test_original_binary_hash_mismatch_blocks(tmp_path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"synthetic-pdf")
    archive = FileFamilyDocumentArchive(tmp_path / "archive")

    with pytest.raises(FamilyDocumentArchiveError, match="source archive hash mismatch"):
        archive.archive(record(source_sha256="0" * 64), source_path=source)


class _FakeGateway:
    def __init__(self, value):
        self.value = value
        self.commands = []

    def execute(self, request):
        self.commands.append(request["command"])
        if request["command"] == "skeleton.memory.private_mutate":
            return {"payload": {"status": "DONE", "canonical_revision": 7}}
        return {
            "payload": {
                "authoritative": True,
                "canonical_revision": 7,
                "value": self.value,
            }
        }


def test_composite_archive_commits_memory_only_after_original_readback(tmp_path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"synthetic-pdf")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    value = record(source_sha256=source_hash)
    gateway = _FakeGateway(value)
    composite = CompositeFamilyDocumentArchive(
        FileFamilyDocumentArchive(tmp_path / "archive"),
        MemoryGatewayFamilyDocumentArchive(gateway),
    )

    receipt = composite.archive(value, source_path=source)

    assert receipt["authoritative"] is True
    assert gateway.commands == [
        "skeleton.memory.private_mutate",
        "skeleton.memory.private_read_exact",
    ]


def test_memory_exact_readback_mismatch_blocks(tmp_path) -> None:
    del tmp_path
    value = record()
    gateway = _FakeGateway({**value, "record_hash": "different"})
    archive = MemoryGatewayFamilyDocumentArchive(gateway)

    with pytest.raises(FamilyDocumentArchiveError, match="MemoryGateway exact readback mismatch"):
        archive.archive(value)
