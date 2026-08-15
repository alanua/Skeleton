from __future__ import annotations

import pytest

from core.family_document_sinks import FamilyDocumentArchiveError, FileFamilyDocumentArchive


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
