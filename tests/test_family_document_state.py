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


def test_concurrent_claims_and_expired_owner_fencing(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    db = tmp_path / "state.sqlite3"
    state = FamilyDocumentReceiptOutbox(db)
    other = FamilyDocumentReceiptOutbox(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(lambda adapter: adapter.claim_work("synthetic", "b" * 64, now=100, lease_seconds=10), (state, other)))
    assert sum(token is not None for token in tokens) == 1
    old = next(token for token in tokens if token)
    assert other.claim_work("synthetic", "b" * 64, now=110) is None
    assert other.work_state_counts() == {"RETRY": 1}
    assert other.claim_work("synthetic", "b" * 64, now=139) is None
    new = other.claim_work("synthetic", "b" * 64, now=140)
    assert new and new != old
    state.mark_work_done("synthetic", "b" * 64, token=old)
    assert state.work_state_counts() == {"CLAIMED": 1}
    other.mark_work_done("synthetic", "b" * 64, token=new)
    assert state.claim_work("synthetic", "b" * 64, now=9999) is None
    assert state.work_state_counts() == {"DONE": 1}


def test_expired_archive_claim_stays_review(tmp_path):
    import time

    state = FamilyDocumentReceiptOutbox(tmp_path / "state.sqlite3")
    now = int(time.time())
    token = state.claim_work("synthetic", "b" * 64, now=now, lease_seconds=10)
    state.begin_archive("synthetic", "b" * 64, token)
    restarted = FamilyDocumentReceiptOutbox(state.db_path)
    assert restarted.claim_work("synthetic", "b" * 64, now=now + 11) is None
    assert restarted.claim_work("synthetic", "b" * 64, now=now + 9999) is None
    assert restarted.work_state_counts() == {"REVIEW": 1}


def test_repeated_expiration_has_bounded_attempts(tmp_path):
    state = FamilyDocumentReceiptOutbox(tmp_path / "state.sqlite3")
    now = 100
    for _ in range(5):
        assert state.claim_work("synthetic", "b" * 64, now=now, lease_seconds=1)
        assert state.claim_work("synthetic", "b" * 64, now=now + 1) is None
        now += 3602
    assert state.work_state_counts() == {"REVIEW": 1}