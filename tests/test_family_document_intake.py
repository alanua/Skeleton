from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from core.family_document_intake import FamilyDocumentIntake, FamilyDocumentIntakeConfig


class RecordingGateway:
    def __init__(self, *, fail_projection: bool = False) -> None:
        self.requests: list[Mapping[str, Any]] = []
        self.fail_projection = fail_projection

    def execute(self, request: Mapping[str, Any]) -> dict[str, object]:
        self.requests.append(request)
        return {
            "payload": {
                "canonical_ref": "family_document:sha256-test",
                "canonical_revision": 1,
                "degraded_indexes": ["synthetic-projection"] if self.fail_projection else [],
            }
        }


def config(tmp_path: Path) -> FamilyDocumentIntakeConfig:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return FamilyDocumentIntakeConfig(
        inbox_roots=(inbox,),
        archive_root=tmp_path / "archive",
        runtime_root=tmp_path / "runtime",
        quarantine_root=tmp_path / "quarantine",
        subject_aliases=("person-a", "person-b", "person-c"),
        stable_age_seconds=0,
        approval_ref="synthetic-test",
    )


def test_accept_archives_before_gateway_and_duplicate_replay_is_noop(tmp_path) -> None:
    cfg = config(tmp_path)
    source = cfg.inbox_roots[0] / "residence-notice.txt"
    source.write_text(
        "Issuer: Synthetic Office residence notice 2026-07-20 appointment",
        encoding="utf-8",
    )
    gateway = RecordingGateway()
    intake = FamilyDocumentIntake(cfg, gateway)
    receipt = intake.process_file(source)
    assert receipt["status"] == "DONE"
    assert len(list(cfg.archive_root.glob("*.json"))) == 1
    assert len(gateway.requests) == 1
    replay = intake.process_file(source)
    assert replay["status"] == "DUPLICATE"
    assert len(list(cfg.archive_root.glob("*.json"))) == 1
    assert len(gateway.requests) == 1


def test_ambiguous_or_incomplete_classification_quarantines_without_gateway(tmp_path) -> None:
    cfg = config(tmp_path)
    source = cfg.inbox_roots[0] / "scan.txt"
    source.write_text("unlabeled synthetic text", encoding="utf-8")
    gateway = RecordingGateway()
    receipt = FamilyDocumentIntake(cfg, gateway).process_file(source)
    assert receipt["status"] == "REVIEW"
    assert len(gateway.requests) == 0
    assert len(list(cfg.quarantine_root.glob("*.json"))) == 1


def test_canonical_commit_survives_degraded_projection(tmp_path) -> None:
    cfg = config(tmp_path)
    source = cfg.inbox_roots[0] / "court.txt"
    source.write_text(
        "Issuer: Synthetic Court court hearing notice 2026-10-02 hearing",
        encoding="utf-8",
    )
    gateway = RecordingGateway(fail_projection=True)
    receipt = FamilyDocumentIntake(cfg, gateway).process_file(source)
    assert receipt["status"] == "DONE"
    assert (cfg.runtime_root / "family_document_projection_outbox.jsonl").exists()


def test_dry_run_reconcile_is_aggregate_only(tmp_path) -> None:
    cfg = config(tmp_path)
    receipt = FamilyDocumentIntake(cfg, RecordingGateway()).reconcile_dry_run()
    assert receipt["mode"] == "dry_run"
    assert receipt["privacy"] == "aggregate_only"
