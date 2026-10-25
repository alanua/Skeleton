from __future__ import annotations

from core.family_document_state import FamilyDocumentReceiptOutbox


def test_state_adapter_restart_preserves_done_and_retry(tmp_path) -> None:
    db = tmp_path / "state.sqlite3"
    state = FamilyDocumentReceiptOutbox(db)
    assert state.should_process("source", "a" * 64, now=100)
    state.mark_work_failure("source", "a" * 64, "temporary", now=100, base_delay_seconds=10)
    assert not state.should_process("source", "a" * 64, now=109)
    assert state.should_process("source", "a" * 64, now=110)

    restarted = FamilyDocumentReceiptOutbox(db)
    restarted.mark_work_done("source", "a" * 64)
    assert not restarted.should_process("source", "a" * 64, now=999)
    assert restarted.work_state_counts() == {"DONE": 1}


def test_multipart_delivery_replay_sends_only_pending_parts(tmp_path) -> None:
    state = FamilyDocumentReceiptOutbox(tmp_path / "state.sqlite3")
    state.enqueue({"receipt_key": "package:x:0001", "message": "one"})
    state.enqueue({"receipt_key": "package:x:0002", "message": "two"})
    sent: list[str] = []

    result = state.drain(sender=sent.append)
    again = FamilyDocumentReceiptOutbox(state.db_path).drain(sender=sent.append)

    assert result["sent"] == 2
    assert again["sent"] == 0
    assert sent == ["one", "two"]
    assert state.state_counts() == {"DONE": 2}


def test_scan_session_restart_and_durable_transitions(tmp_path):
    import sqlite3
    from tests.test_family_document_scan_session import fixture_events

    db = tmp_path / 'state.sqlite3'
    events = fixture_events()
    for event in events:
        state = FamilyDocumentReceiptOutbox(db)
        state.record_scan_event(event)
        assert state.record_scan_event(event) == state.scan_session(event.session_id)
    restarted = FamilyDocumentReceiptOutbox(db)
    session = restarted.scan_session(events[0].session_id)
    assert session.state == 'FINALIZED'
    assert len(session.slots) == 14
    with sqlite3.connect(db) as connection:
        rows = connection.execute('SELECT from_state, to_state FROM family_document_scan_events ORDER BY sequence').fetchall()
    assert rows == [(None, 'OPEN'), ('OPEN', 'OPEN'), ('OPEN', 'OPEN'), ('OPEN', 'FINALIZED')]


def test_scan_explicit_identity_survives_long_gap_and_isolation(tmp_path, monkeypatch):
    from dataclasses import replace
    import pytest
    from core.family_document_scan_session import ScanEvent
    from tests.test_family_document_scan_session import fixture_events

    db = tmp_path / 'state.sqlite3'
    start, fronts, backs, done = fixture_events()
    monkeypatch.setattr('core.family_document_state.time.time', lambda: 100)
    state = FamilyDocumentReceiptOutbox(db)
    state.record_scan_event(start)
    state.record_scan_event(fronts)
    monkeypatch.setattr('core.family_document_state.time.time', lambda: 701)
    state = FamilyDocumentReceiptOutbox(db)
    assert len(state.record_scan_event(backs).slots) == 14
    with pytest.raises(ValueError, match='mismatch'):
        state.record_scan_event(replace(done, profile_id='other-profile'))
    other = replace(start, session_id='other-session', profile_id='other-profile')
    assert state.record_scan_event(other).slots == ()
    assert state.record_scan_event(done).state == 'FINALIZED'
    with pytest.raises(ValueError, match='immutable'):
        state.record_scan_event(replace(backs, event_id='new-event', pass_id='new-pass'))
    assert state.scan_session(other.session_id).state == 'OPEN'

    stale_start = replace(start, session_id='stale-session')
    state.record_scan_event(stale_start)
    state.record_scan_event(ScanEvent('timeout', stale_start.session_id, stale_start.profile_id, 'stale', 'elapsed'))
    assert FamilyDocumentReceiptOutbox(db).scan_session(stale_start.session_id).state == 'REVIEW'