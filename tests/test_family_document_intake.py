from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Mapping

from core.family_document_intake import IntakeRequest, deterministic_relative_path, process_intake
from core.family_document_runtime import InMemoryCalendarSink, InMemoryGatewaySink, public_token
from core.family_document_taxonomy import Person, RouteDecision


class CountingArchive:
    def __init__(self) -> None:
        self.calls = 0
        self.verified_before_memory = False

    def put(self, **kwargs: object) -> dict[str, object]:
        self.calls += 1
        self.verified_before_memory = True
        return {"status": "WRITTEN", "verified": True, "path_token": "archive-token"}


class OrderingMemory(InMemoryGatewaySink):
    def __init__(self, archive: CountingArchive) -> None:
        super().__init__()
        self.archive = archive

    def commit(self, record: Mapping[str, object]) -> dict[str, object]:
        assert self.archive.verified_before_memory is True
        return super().commit(record)


def test_unstable_and_partial_files_are_not_processed(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    unstable = root / "new.txt"
    partial = root / "scan.pdf.crdownload"
    unstable.write_text("Synthetic Jane Example tax 2024-05-01", encoding="utf-8")
    partial.write_text("Synthetic Jane Example tax 2024-05-01", encoding="utf-8")
    request = _request(tmp_path, [unstable, partial], stable_after_seconds=999)
    memory = InMemoryGatewaySink()

    result = process_intake(request, memory_sink=memory)

    assert result["status"] == "PARTIAL"
    assert [receipt["reason_code"] for receipt in result["receipts"]] == ["UNSTABLE_FILE", "PARTIAL_FILE"]
    assert memory.writes == 0


def test_restart_resumes_without_duplicate_archive_memory_or_calendar_writes(tmp_path: Path) -> None:
    source = _stable_doc(tmp_path / "mfp" / "doc.txt", "Synthetic Jane Example tax appointment 2024-05-01 IRS")
    request = _request(tmp_path, [source], roots=[source.parent])
    archive = CountingArchive()
    memory = OrderingMemory(archive)
    calendar = InMemoryCalendarSink()

    first = process_intake(request, archive_sink=archive, memory_sink=memory, calendar_sink=calendar)
    second = process_intake(request, archive_sink=archive, memory_sink=memory, calendar_sink=calendar)

    assert first["status"] == "DONE"
    assert second["receipts"][0]["idempotency"] == "DUPLICATE_EXISTING"
    assert archive.calls == 1
    assert memory.writes == 1
    assert calendar.writes == 1


def test_identical_binary_from_mfp_windows_and_drive_becomes_one_cluster(tmp_path: Path) -> None:
    content = "Synthetic Jane Example tax 2024-05-01 IRS"
    files = [
        _stable_doc(tmp_path / "mfp" / "scan.txt", content),
        _stable_doc(tmp_path / "windows_inbox" / "scan.txt", content),
        _stable_doc(tmp_path / "drive_inbox" / "scan.txt", content),
    ]
    request = _request(tmp_path, files, roots=[path.parent for path in files])
    memory = InMemoryGatewaySink()
    archive = CountingArchive()

    result = process_intake(request, memory_sink=memory, archive_sink=archive)

    assert result["status"] == "DONE"
    assert memory.writes == 1
    assert archive.calls == 1
    assert len({receipt["cluster_token"] for receipt in result["receipts"]}) == 1


def test_fixed_hierarchy_and_deterministic_filename_collision() -> None:
    route = RouteDecision(status="READY", person_id="jane-example", topic="tax", country="de")
    path = deterministic_relative_path(
        route=route,
        document_date=__import__("datetime").date(2024, 5, 1),
        source_suffix=".PDF",
        content_sha256="a" * 64,
    )
    collided = deterministic_relative_path(
        route=route,
        document_date=__import__("datetime").date(2024, 5, 1),
        source_suffix=".PDF",
        content_sha256="a" * 64,
        occupied_names={path.name},
    )

    assert path.parts == ("jane-example", "tax", "de", "2024", "2024-05-01_tax_aaaaaaaaaaaa.pdf")
    assert collided.name == "2024-05-01_tax_aaaaaaaaaaaa-2.pdf"


def test_surname_only_and_multi_owner_ambiguity_route_to_review(tmp_path: Path) -> None:
    surname_only = _stable_doc(tmp_path / "inbox" / "surname.txt", "Synthetic Example tax 2024-05-01")
    multi = _stable_doc(tmp_path / "inbox" / "multi.txt", "Synthetic Jane Example and John Example tax 2024-05-01")
    request = _request(
        tmp_path,
        [surname_only, multi],
        people=(
            Person("p1", "Jane Example", ("Example",), "DE"),
            Person("p2", "John Example", ("Example",), "DE"),
        ),
    )

    result = process_intake(request)

    assert [receipt["route_status"] for receipt in result["receipts"]] == ["REVIEW", "REVIEW"]
    assert [receipt["reason_code"] for receipt in result["receipts"]] == ["OWNER_AMBIGUOUS", "OWNER_AMBIGUOUS"]


def test_fail_visible_cases_and_reject_roots_before_reads(tmp_path: Path) -> None:
    root = tmp_path / "inbox"
    root.mkdir()
    unsupported = _stable_doc(root / "doc.bin", "synthetic")
    encrypted = root / "encrypted.pdf"
    encrypted.write_bytes(b"%PDF /Encrypt synthetic\n%%EOF")
    _old(encrypted)
    secret = tmp_path / "secret" / "hidden.txt"
    secret.parent.mkdir()
    request = _request(tmp_path, [unsupported, encrypted, secret], roots=[root, secret.parent])

    result = process_intake(request, ocr_recognizer=lambda path: "Synthetic Jane Example tax 2024-05-01")

    assert [receipt["reason_code"] for receipt in result["receipts"]] == [
        "UNSUPPORTED_DOCUMENT_TYPE",
        "DOCUMENT_UNREADABLE",
        "SOURCE_ROOT_REJECTED",
    ]


def test_archive_verified_before_memory_and_projection_failure_is_restart_safe(tmp_path: Path) -> None:
    source = _stable_doc(tmp_path / "inbox" / "doc.txt", "Synthetic Jane Example tax 2024-05-01 IRS")
    request = _request(tmp_path, [source])
    archive = CountingArchive()
    memory = OrderingMemory(archive)

    first = process_intake(request, archive_sink=archive, memory_sink=memory, projection_sink=lambda record: (_ for _ in ()).throw(RuntimeError("synthetic")))
    second = process_intake(request, archive_sink=archive, memory_sink=memory, projection_sink=lambda record: {"ok": True})

    assert first["receipts"][0]["status"] == "PARTIAL"
    assert first["receipts"][0]["reason_code"] == "PROJECTION_FAILED"
    assert second["status"] == "DONE"
    assert archive.calls == 1
    assert memory.writes == 1


def test_canonical_commit_uses_injected_memory_gateway_sink_only(tmp_path: Path) -> None:
    source = _stable_doc(tmp_path / "inbox" / "doc.txt", "Synthetic Jane Example tax 2024-05-01 IRS")
    memory = InMemoryGatewaySink()

    process_intake(_request(tmp_path, [source]), memory_sink=memory)

    assert memory.writes == 1
    assert next(iter(memory.records.values()))["schema"] == "skeleton.family_document_record.v1"


def test_calendar_upsert_selects_only_semantic_events_and_is_idempotent(tmp_path: Path) -> None:
    semantic = _stable_doc(tmp_path / "inbox" / "semantic.txt", "Synthetic Jane Example tax appointment 2024-05-01 IRS")
    quiet = _stable_doc(tmp_path / "inbox" / "quiet.txt", "Synthetic Jane Example tax 2024-06-01 IRS")
    calendar = InMemoryCalendarSink()

    process_intake(_request(tmp_path, [semantic, quiet]), calendar_sink=calendar)
    process_intake(_request(tmp_path, [semantic, quiet]), calendar_sink=calendar)

    assert calendar.writes == 1
    event = next(iter(calendar.events.values()))
    assert not {"attendees", "conferenceData", "hangoutLink", "sendUpdates"}.intersection(event)


def test_public_receipts_and_journal_do_not_leak_private_material(tmp_path: Path) -> None:
    source = _stable_doc(tmp_path / "inbox" / "private-name.txt", "Synthetic Jane Example tax appointment 2024-05-01 PRIVATE-OCR")
    result = process_intake(_request(tmp_path, [source]))
    public = json.dumps(result, sort_keys=True)
    journal = (tmp_path / "journal.json").read_text(encoding="utf-8")

    for leaked in (str(source), "Jane", "Example", "PRIVATE-OCR", "sha256:", "private-name"):
        assert leaked not in public
    for leaked in (str(source), "Jane", "Example", "PRIVATE-OCR", "private-name"):
        assert leaked not in journal
    assert public_token(str(source)) not in public


def test_dry_run_performs_no_side_effects(tmp_path: Path) -> None:
    source = _stable_doc(tmp_path / "inbox" / "doc.txt", "Synthetic Jane Example tax appointment 2024-05-01 IRS")
    archive = CountingArchive()
    memory = InMemoryGatewaySink()
    calendar = InMemoryCalendarSink()
    request = _request(tmp_path, [source], dry_run=True)

    result = process_intake(request, archive_sink=archive, memory_sink=memory, calendar_sink=calendar)

    assert result["status"] == "DRY_RUN"
    assert result["receipts"][0]["side_effects"] == []
    assert archive.calls == 0
    assert memory.writes == 0
    assert calendar.writes == 0


def _request(
    tmp_path: Path,
    sources: list[Path],
    *,
    roots: list[Path] | None = None,
    people: tuple[Person, ...] = (Person("p1", "Jane Example", ("Example",), "DE"),),
    dry_run: bool = False,
    stable_after_seconds: float = 0,
) -> IntakeRequest:
    return IntakeRequest(
        source_paths=tuple(sources),
        intake_roots=tuple(roots or [tmp_path / "inbox"]),
        archive_root=tmp_path / "archive",
        journal_path=tmp_path / "journal.json",
        people=people,
        dry_run=dry_run,
        stable_after_seconds=stable_after_seconds,
    )


def _stable_doc(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _old(path)
    return path


def _old(path: Path) -> None:
    old = time.time() - 10
    os.utime(path, (old, old))
