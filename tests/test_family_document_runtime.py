from __future__ import annotations

from pathlib import Path

import pytest

from core.family_document_runtime import DurableJournal, FamilyDocumentWorker, ProjectionOutbox, RuntimeErrorCode, RuntimeLimits
from core.family_document_sources import ApprovedRoot


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class Processor:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result or {"status": "DONE", "reason_code": "projection_pending", "counts": {"written": 1}}
        self.error = error
        self.calls: list[Path] = []

    def process(self, source: Path, *, dry_run: bool = False):
        del dry_run
        self.calls.append(source)
        if self.error:
            raise self.error
        return self.result


def prepared(tmp_path: Path, *, clock: Clock, attempts: int = 3):
    root = tmp_path / "inbox"
    root.mkdir()
    source = root / "scan.pdf"
    source.write_bytes(b"pdf")
    limits = RuntimeLimits(settle_seconds=2, lease_seconds=30, max_attempts=attempts, retry_base_seconds=5)
    journal = DurableJournal(tmp_path / "journal.json", limits, clock=clock)
    roots = (ApprovedRoot("mfp", root),)
    return root, source, journal, roots


def test_worker_settles_claims_and_completes_without_stale_lock(tmp_path: Path) -> None:
    clock = Clock()
    _, source, journal, roots = prepared(tmp_path, clock=clock)
    processor = Processor()
    worker = FamilyDocumentWorker(roots=roots, journal=journal, processor=processor, lock_path=tmp_path / "worker.lock")
    assert worker.run_once()["operation"] == "IDLE"
    clock.advance(2)
    second = worker.run_once()
    assert second["operation"] == "PROCESSED"
    assert processor.calls == [source.resolve()]
    assert second["queue_counts"]["DONE"] == 1
    assert worker.run_once()["operation"] == "IDLE"


def test_expired_processing_lease_recovers_after_crash(tmp_path: Path) -> None:
    clock = Clock()
    _, _, journal, roots = prepared(tmp_path, clock=clock)
    journal.discover(roots)
    journal.settle(roots)
    clock.advance(2)
    journal.settle(roots)
    assert journal.claim("worker-a") is not None
    clock.advance(31)
    assert journal.recover_expired() == 1
    item = next(iter(journal.store.snapshot()["items"].values()))
    assert item["state"] == "RETRY"
    assert item["reason_code"] == "lease_expired"


def test_retry_backoff_and_quarantine_are_durable(tmp_path: Path) -> None:
    clock = Clock()
    _, _, journal, roots = prepared(tmp_path, clock=clock, attempts=2)
    journal.discover(roots)
    journal.settle(roots)
    clock.advance(2)
    journal.settle(roots)
    key, _ = journal.claim("worker-a")
    assert journal.fail(key, "worker-a", "processing_failed") == "RETRY"
    clock.advance(5)
    journal.settle(roots)
    key, _ = journal.claim("worker-a")
    assert journal.fail(key, "worker-a", "processing_failed") == "QUARANTINED"
    health = journal.health()
    assert health["status"] == "BLOCKED"
    assert health["queue_counts"]["QUARANTINED"] == 1


def test_single_instance_lock_blocks_parallel_worker(tmp_path: Path) -> None:
    clock = Clock()
    _, _, journal, roots = prepared(tmp_path, clock=clock)
    first = FamilyDocumentWorker(roots=roots, journal=journal, processor=Processor(), lock_path=tmp_path / "worker.lock")
    second = FamilyDocumentWorker(roots=roots, journal=journal, processor=Processor(), lock_path=tmp_path / "worker.lock")
    with first.single_instance():
        with pytest.raises(RuntimeErrorCode) as exc:
            with second.single_instance():
                pass
    assert exc.value.reason_code == "worker_already_running"


def test_projection_outbox_retries_recovers_and_quarantines(tmp_path: Path) -> None:
    clock = Clock()
    outbox = ProjectionOutbox(tmp_path / "outbox.json", clock=clock)
    outbox.enqueue("fact:projection", "a" * 64)
    assert outbox.process_one(lambda key, digest: False, max_attempts=2) == "RETRY"
    clock.advance(30)
    assert outbox.process_one(lambda key, digest: False, max_attempts=2) == "QUARANTINED"
    assert outbox.counts() == {"QUARANTINED": 1}
    outbox.enqueue("other:projection", "b" * 64)
    assert outbox.process_one(lambda key, digest: True) == "DONE"
    assert outbox.counts()["DONE"] == 1
