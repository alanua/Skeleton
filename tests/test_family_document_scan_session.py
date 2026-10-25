import pytest

from core.family_document_scan_session import PageSlot, ScanEvent, apply_event


def fixture_events():
    return (
        ScanEvent('open', 'synthetic-session', 'duplex-profile', 'open', 'operator-start'),
        ScanEvent('fronts', 'synthetic-session', 'duplex-profile', 'pass', 'explicit-sheet-map', 'pass-front',
                  tuple(PageSlot(f'f{i}', i, 'front') for i in range(1, 9))),
        ScanEvent('backs', 'synthetic-session', 'duplex-profile', 'pass', 'explicit-sheet-map', 'pass-back',
                  tuple(PageSlot(f'b{i}', i, 'back', 'blank' if i == 6 else 'page') for i in range(6, 0, -1))),
        ScanEvent('done', 'synthetic-session', 'duplex-profile', 'complete', 'operator-complete', absent_backs=(7, 8)),
    )


def test_unequal_physical_slots_and_immutable_completion():
    session = None
    for event in fixture_events():
        session = apply_event(session, event)
    assert session.state == 'FINALIZED'
    assert len(session.slots) == 14
    assert [s.page_id for s in session.slots] == [f'f{i}' for i in range(1, 9)] + [f'b{i}' for i in range(6, 0, -1)]
    assert session.slots[8].kind == 'blank'
    assert session.absent_backs == (7, 8)
    for event in fixture_events():
        assert apply_event(session, event) == session
    with pytest.raises(ValueError, match='immutable'):
        apply_event(session, ScanEvent('late', session.session_id, session.profile_id, 'stale', 'timeout'))


def test_timeout_and_uncertain_evidence_cannot_finalize():
    events = fixture_events()
    for uncertain in (ScanEvent('stale', events[0].session_id, events[0].profile_id, 'stale', 'timeout'),
                      ScanEvent('unknown', events[0].session_id, events[0].profile_id, 'pass', '', 'unknown')):
        session = None
        for event in (*events[:3], uncertain, events[3]):
            session = apply_event(session, event)
        assert session.state == 'REVIEW'


def test_identity_and_mapping_conflicts():
    start, front, back, done = fixture_events()
    session = apply_event(None, start)
    for field in ('session_id', 'profile_id'):
        from dataclasses import replace
        with pytest.raises(ValueError, match='mismatch'):
            apply_event(session, replace(front, **{field: 'different'}))
    session = apply_event(session, front)
    assert apply_event(session, done).state == 'REVIEW'
    conflict = ScanEvent('conflict', start.session_id, start.profile_id, 'pass', 'explicit', 'other', (PageSlot('other', 1, 'front'),))
    assert apply_event(session, conflict).state == 'REVIEW'
    with pytest.raises(ValueError, match='reused'):
        apply_event(session, replace(front, evidence='changed'))