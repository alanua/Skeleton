from __future__ import annotations

import json

import pytest

from core.reading.models import (
    Edition,
    ProgressCheckpoint,
    ReadingContractError,
    ReadingFormat,
    ReadingReceipt,
    ReadingSession,
    WorkIdentity,
    reconcile_checkpoints,
)


def assert_no_public_private_fields(value: object) -> None:
    rendered = json.dumps(value, sort_keys=True)
    forbidden_keys = (
        "work_ref",
        "edition_ref",
        "session_ref",
        "checkpoint_ref",
        "source_work_hash",
        "edition_hash",
        "started_at",
        "observed_at",
        "page_current",
        "page_total",
        "percent",
        "time_position_seconds",
        "duration_seconds",
        "chapter_ref",
    )
    for key in forbidden_keys:
        assert key not in rendered


def synthetic_ebook() -> tuple[WorkIdentity, Edition, ReadingSession]:
    work = WorkIdentity.new(
        source_namespace="synthetic",
        source_work_key="synthetic-work-1",
    )
    edition = Edition.new(
        work_identity=work,
        reading_format=ReadingFormat.EPUB,
        edition_key="synthetic-epub-edition",
    )
    session = ReadingSession.new(
        edition=edition,
        frontend="MOON_READER",
        started_at=100,
    )
    return work, edition, session


def synthetic_audio() -> tuple[WorkIdentity, Edition, ReadingSession]:
    work = WorkIdentity.new(
        source_namespace="synthetic",
        source_work_key="synthetic-audio-work",
    )
    edition = Edition.new(
        work_identity=work,
        reading_format=ReadingFormat.AUDIOBOOK,
        edition_key="synthetic-audio-edition",
    )
    session = ReadingSession.new(
        edition=edition,
        frontend="SMART_AUDIOBOOK_PLAYER",
        started_at=200,
    )
    return work, edition, session


def test_work_and_edition_references_are_stable_without_private_titles() -> None:
    work_a = WorkIdentity.new(source_namespace="synthetic", source_work_key="private-book-key")
    work_b = WorkIdentity.new(source_namespace="synthetic", source_work_key="private-book-key")
    edition_a = Edition.new(
        work_identity=work_a,
        reading_format="PDF",
        edition_key="private-edition-key",
    )
    edition_b = Edition.new(
        work_identity=work_b,
        reading_format="PDF",
        edition_key="private-edition-key",
    )

    assert work_a.work_ref == work_b.work_ref
    assert edition_a.edition_ref == edition_b.edition_ref
    public = edition_a.to_public_mapping()
    rendered = json.dumps(public)
    assert "private-book-key" not in rendered
    assert "private-edition-key" not in rendered
    assert work_a.source_work_hash not in rendered
    assert edition_a.edition_hash not in rendered
    assert work_a.work_ref not in rendered
    assert edition_a.edition_ref not in rendered
    assert_no_public_private_fields(public)


def test_private_projection_keeps_local_refs_under_private_storage_semantics() -> None:
    work, edition, session = synthetic_ebook()
    checkpoint = ProgressCheckpoint.ebook(
        session=session,
        edition=edition,
        observed_at=120,
        page_current=10,
        page_total=100,
        percent=10.0,
    )
    receipt = ReadingReceipt(
        work_identity=work,
        edition=edition,
        session=session,
        checkpoints=(checkpoint,),
    )

    private = receipt.to_private_mapping()
    rendered = json.dumps(private)

    assert private["storage_semantics"] == "PRIVATE_LOCAL_ONLY"
    assert private["work_identity"]["storage_semantics"] == "PRIVATE_LOCAL_ONLY"
    assert private["edition"]["storage_semantics"] == "PRIVATE_LOCAL_ONLY"
    assert private["session"]["storage_semantics"] == "PRIVATE_LOCAL_ONLY"
    assert private["checkpoints"][0]["storage_semantics"] == "PRIVATE_LOCAL_ONLY"
    assert work.work_ref in rendered
    assert work.source_work_hash in rendered
    assert edition.edition_ref in rendered
    assert edition.edition_hash in rendered
    assert session.session_ref in rendered
    assert checkpoint.checkpoint_ref in rendered
    assert '"observed_at": 120' in rendered
    assert '"page_current": 10' in rendered


def test_ebook_progress_uses_page_percent_shape_only() -> None:
    _, edition, session = synthetic_ebook()

    checkpoint = ProgressCheckpoint.ebook(
        session=session,
        edition=edition,
        observed_at=120,
        page_current=10,
        page_total=100,
        percent=10.0,
    )

    assert checkpoint.progress_kind == "EBOOK_PAGE_PERCENT"
    assert checkpoint.time_position_seconds is None
    with pytest.raises(ReadingContractError) as exc:
        ProgressCheckpoint(
            checkpoint_ref=checkpoint.checkpoint_ref,
            session_ref=session.session_ref,
            edition_ref=edition.edition_ref,
            reading_format=edition.reading_format,
            observed_at=121,
            progress_kind="EBOOK_PAGE_PERCENT",
            page_current=11,
            time_position_seconds=60,
        )
    assert exc.value.reason_code == "EBOOK_AUDIO_FIELDS_PRESENT"


def test_audiobook_progress_uses_time_chapter_shape_only() -> None:
    _, edition, session = synthetic_audio()

    checkpoint = ProgressCheckpoint.audiobook(
        session=session,
        edition=edition,
        observed_at=240,
        time_position_seconds=180,
        duration_seconds=3600,
        chapter_ref="chapter:01",
    )

    assert checkpoint.progress_kind == "AUDIO_TIME_CHAPTER"
    assert checkpoint.page_current is None
    with pytest.raises(ReadingContractError) as exc:
        ProgressCheckpoint(
            checkpoint_ref=checkpoint.checkpoint_ref,
            session_ref=session.session_ref,
            edition_ref=edition.edition_ref,
            reading_format=edition.reading_format,
            observed_at=241,
            progress_kind="AUDIO_TIME_CHAPTER",
            time_position_seconds=181,
            percent=5,
        )
    assert exc.value.reason_code == "AUDIO_EBOOK_FIELDS_PRESENT"


def test_unknown_unavailable_are_explicit_and_positionless() -> None:
    work = WorkIdentity.unresolved(source_namespace="synthetic", identity_state="UNKNOWN")
    edition = Edition.unresolved(
        work_identity=work,
        reading_format=ReadingFormat.UNKNOWN,
        edition_state="UNAVAILABLE",
    )
    session = ReadingSession.new(
        edition=edition,
        frontend="SYNTHETIC",
        started_at=10,
    )
    checkpoint = ProgressCheckpoint.unresolved(
        session=session,
        edition=edition,
        observed_at=11,
        progress_kind="UNKNOWN",
    )

    assert checkpoint.progress_kind == "UNKNOWN"
    with pytest.raises(ReadingContractError) as exc:
        ProgressCheckpoint(
            checkpoint_ref=checkpoint.checkpoint_ref,
            session_ref=session.session_ref,
            edition_ref=edition.edition_ref,
            reading_format=edition.reading_format,
            observed_at=11,
            progress_kind="UNKNOWN",
            percent=1,
        )
    assert exc.value.reason_code == "UNRESOLVED_PROGRESS_HAS_POSITION"


def test_duplicate_checkpoint_reconciliation_is_idempotent() -> None:
    _, edition, session = synthetic_ebook()
    first = ProgressCheckpoint.ebook(
        session=session,
        edition=edition,
        observed_at=120,
        page_current=10,
        page_total=100,
    )
    second = ProgressCheckpoint.ebook(
        session=session,
        edition=edition,
        observed_at=130,
        page_current=11,
        page_total=100,
    )

    assert reconcile_checkpoints([second, first, first]) == (first, second)

    conflicting = ProgressCheckpoint(
        checkpoint_ref=first.checkpoint_ref,
        session_ref=session.session_ref,
        edition_ref=edition.edition_ref,
        reading_format=edition.reading_format,
        observed_at=120,
        progress_kind="EBOOK_PAGE_PERCENT",
        page_current=12,
        page_total=100,
    )
    with pytest.raises(ReadingContractError) as exc:
        reconcile_checkpoints([first, conflicting])
    assert exc.value.reason_code == "DUPLICATE_CHECKPOINT_CONFLICT"


def test_receipt_is_public_safe_and_monotonic() -> None:
    work, edition, session = synthetic_audio()
    checkpoint = ProgressCheckpoint.audiobook(
        session=session,
        edition=edition,
        observed_at=201,
        time_position_seconds=1,
        chapter_ref="chapter:01",
    )

    receipt = ReadingReceipt(
        work_identity=work,
        edition=edition,
        session=session,
        checkpoints=(checkpoint, checkpoint),
    )
    public = receipt.to_public_mapping()
    rendered = json.dumps(public)

    assert public["schema"] == "skeleton.reading_gateway.receipt.v1"
    assert public["privacy_boundary"] == "PRIVATE_READING_STATE_LOCAL_PUBLIC_AGGREGATES_ONLY"
    assert public["work_count"] == 1
    assert public["edition_count"] == 1
    assert public["session_count"] == 1
    assert public["checkpoint_count"] == 1
    assert public["checkpoint_kind_counts"] == {"AUDIO_TIME_CHAPTER": 1}
    assert public["reading_format_counts"] == {"AUDIOBOOK": 1}
    assert public["frontend_counts"] == {"SMART_AUDIOBOOK_PLAYER": 1}
    assert public["session_status_counts"] == {"ACTIVE": 1}
    assert public["identity_state_counts"] == {"KNOWN": 1}
    assert public["edition_state_counts"] == {"KNOWN": 1}
    assert public["has_progress"] is True
    assert public["private_identifiers_included"] is False
    assert public["android_storage_paths_included"] is False
    assert public["live_device_interactions_included"] is False
    assert "synthetic-audio-work" not in rendered
    assert work.source_work_hash not in rendered
    assert edition.edition_hash not in rendered
    assert work.work_ref not in rendered
    assert edition.edition_ref not in rendered
    assert session.session_ref not in rendered
    assert checkpoint.checkpoint_ref not in rendered
    assert "chapter:01" not in rendered
    assert "time_position_seconds" not in rendered
    assert "observed_at" not in rendered
    assert "Android/data" not in rendered
    assert_no_public_private_fields(public)

    before_session = ProgressCheckpoint.audiobook(
        session=session,
        edition=edition,
        observed_at=199,
        time_position_seconds=1,
    )
    with pytest.raises(ReadingContractError) as exc:
        ReadingReceipt(
            work_identity=work,
            edition=edition,
            session=session,
            checkpoints=(before_session,),
        )
    assert exc.value.reason_code == "CHECKPOINT_BEFORE_SESSION"
