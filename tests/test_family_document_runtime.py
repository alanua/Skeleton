from __future__ import annotations

import pytest

from core.family_document_intake import FamilyDocumentIntake, FamilyDocumentIntakeConfig
from core.family_document_runtime import FamilyDocumentRuntimeError, FamilyDocumentWorker, single_instance_lock
from tests.test_family_document_intake import RecordingGateway


def config(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return FamilyDocumentIntakeConfig(
        inbox_roots=(inbox,),
        archive_root=tmp_path / "archive",
        runtime_root=tmp_path / "runtime",
        quarantine_root=tmp_path / "quarantine",
        subject_aliases=("person-a", "person-b", "person-c"),
        stable_age_seconds=0,
    )


def test_single_instance_lock_blocks_second_holder(tmp_path) -> None:
    with single_instance_lock(tmp_path):
        with pytest.raises(FamilyDocumentRuntimeError):
            with single_instance_lock(tmp_path):
                pass


def test_worker_run_once_returns_idle_aggregate_receipt(tmp_path) -> None:
    cfg = config(tmp_path)
    worker = FamilyDocumentWorker(cfg, FamilyDocumentIntake(cfg, RecordingGateway()))
    receipt = worker.run_once()
    assert receipt["status"] == "IDLE"
    assert receipt["privacy"] == "aggregate_only"


def test_worker_retries_with_aggregate_error_receipt(tmp_path) -> None:
    cfg = config(tmp_path)

    class FailingIntake(FamilyDocumentIntake):
        def process_one(self):
            raise RuntimeError("synthetic failure")

    worker = FamilyDocumentWorker(cfg, FailingIntake(cfg, RecordingGateway()), max_attempts=2, backoff_seconds=0)
    receipt = worker.run_once()
    assert receipt["status"] == "ERROR"
    assert receipt["aggregate_counts"]["attempts"] == 2
