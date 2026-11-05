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


def test_quarantine_proposals_bounded_idempotent_and_never_release(tmp_path):
    from tests.test_family_document_local_inference import output, payload
    state = FamilyDocumentReceiptOutbox(tmp_path / "state.sqlite")
    for source in ("a", "b", "c"):
        state.mark_work_failure(source, "a" * 64, "synthetic", max_attempts=1)
    calls = []
    def evaluate(source, sha):
        calls.append(source)
        return output("REVIEW" if source == "b" else "ACCEPT"), payload()
    assert state.propose_quarantine_reevaluation(evaluate, limit=2) == {
        "evaluated": 2, "acceptance_proposed": 1, "review": 1}
    restarted = FamilyDocumentReceiptOutbox(state.db_path)
    assert restarted.propose_quarantine_reevaluation(evaluate, limit=2)["evaluated"] == 1
    assert restarted.propose_quarantine_reevaluation(evaluate)["evaluated"] == 0
    assert calls == ["a", "b", "c"]
    assert restarted.work_state_counts() == {"REVIEW": 3}
    assert restarted.receipts() == []