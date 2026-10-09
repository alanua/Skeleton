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
    rendered = json.dumps(edition_a.to_public_mapping())
    assert "private-book-key" not in rendered
    assert "private-edition-key" not in rendered


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
    assert public["checkpoint_count"] == 1
    assert public["private_identifiers_included"] is False
    assert public["android_storage_paths_included"] is False
    assert public["live_device_interactions_included"] is False
    assert "synthetic-audio-work" not in rendered
    assert "Android/data" not in rendered

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
