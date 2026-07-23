from __future__ import annotations

from pathlib import Path

from core.family_document_runtime import FileArchiveSink, InMemoryCalendarSink, InMemoryGatewaySink, sha256_file


def test_archive_write_is_verified_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "inbox" / "doc.txt"
    source.parent.mkdir()
    source.write_text("Synthetic archive payload", encoding="utf-8")
    digest = sha256_file(source)
    sink = FileArchiveSink(tmp_path / "archive")

    first = sink.put(cluster_id=f"sha256:{digest}", relative_path=Path("owner/tax/de/2024/doc.txt"), source=source, expected_sha256=digest, dry_run=False)
    second = sink.put(cluster_id=f"sha256:{digest}", relative_path=Path("owner/tax/de/2024/doc.txt"), source=source, expected_sha256=digest, dry_run=False)

    assert first["status"] == "WRITTEN"
    assert first["verified"] is True
    assert second["status"] == "DUPLICATE_EXISTING"


def test_injected_memory_sink_is_idempotent() -> None:
    sink = InMemoryGatewaySink()
    record = {"cluster_id": "sha256:" + "a" * 64, "payload": "synthetic"}

    assert sink.commit(record)["status"] == "COMMITTED"
    assert sink.commit(record)["status"] == "DUPLICATE_EXISTING"
    assert sink.writes == 1


def test_calendar_upsert_is_idempotent_and_forbids_external_notification_fields() -> None:
    sink = InMemoryCalendarSink()
    payload = {"schema": "skeleton.family_document_event.v1", "summary": "Family document tax", "date": "2024-05-01"}

    assert sink.upsert("event-1", payload)["status"] == "UPSERTED"
    assert sink.upsert("event-1", payload)["status"] == "DUPLICATE_EXISTING"
    assert sink.writes == 1

    for key in ("attendees", "conferenceData", "hangoutLink", "sendUpdates"):
        blocked = dict(payload)
        blocked[key] = []
        try:
            sink.upsert(f"blocked-{key}", blocked)
        except Exception as exc:
            assert getattr(exc, "reason_code") == "CALENDAR_PRIVACY_VIOLATION"
        else:
            raise AssertionError(f"{key} was not rejected")
