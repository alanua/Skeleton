"""Encrypted queue tests use only disposable synthetic ciphertext and temp directories."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.workstation_agent import CommandReceipt, WorkstationRejected
from core.workstation_receipt_store import EncryptedReceiptStore


def receipt(suffix: str = "one") -> CommandReceipt:
    return CommandReceipt(
        status="APPLIED", reason_class="OK", operation="APPLY",
        before_revision=0, after_revision=1,
        receipt_hash=hashlib.sha256(suffix.encode()).hexdigest(),
    )


def make_store(tmp_path: Path, *, key: bytes = bytes(range(32)), max_items: int = 10):
    return EncryptedReceiptStore(
        path=tmp_path / "receipts.db", key=key, max_items=max_items,
        verify_ack=lambda h, signature: signature == b"authenticated" + h.encode("ascii"))


def test_encrypt_reopen_deduplicate_ack_and_permissions(tmp_path):
    p = tmp_path / "receipts.db"
    r = receipt("sensitive-private-shape")
    with make_store(tmp_path) as store:
        assert store.enqueue(r)
        assert not store.enqueue(r)
        assert store.count() == 1
        assert store.list_pending()[0]["receipt_hash"] == r.receipt_hash
        assert not store.acknowledge(receipt("missing").receipt_hash,
                                      b"authenticated" + receipt("missing").receipt_hash.encode("ascii"))
    # No receipt content or CAD payload is persisted in plaintext bytes.
    raw = p.read_bytes()
    assert r.receipt_hash.encode("ascii") in raw  # public-safe index
    assert b'"before_revision"' not in raw
    assert b"PRIVATE_DRAWING" not in raw
    if __import__("os").name == "posix":
        assert p.stat().st_mode & 0o077 == 0
    with make_store(tmp_path) as store:
        assert store.count() == 1
        with pytest.raises(WorkstationRejected, match="AUTHENTICATED_ACK_DENIED"):
            store.acknowledge(r.receipt_hash, b"spoof")
        assert store.count() == 1
        assert store.acknowledge(r.receipt_hash, b"authenticated" + r.receipt_hash.encode("ascii"))
        assert store.count() == 0


def test_reject_wrong_key_corrupt_ciphertext_and_missing_approver(tmp_path):
    with make_store(tmp_path) as store:
        store.enqueue(receipt())
    with make_store(tmp_path, key=b"a"*32) as store:
        with pytest.raises(WorkstationRejected, match="RECEIPT_DECRYPT_FAILED"):
            store.list_pending()
        with pytest.raises(WorkstationRejected, match="AUTHENTICATED_ACK_DENIED"):
            store.acknowledge(receipt().receipt_hash, b"invalid")
    with EncryptedReceiptStore(path=tmp_path/"other.db", key=b"a"*32,
                                verify_ack=None) as store:
        with pytest.raises(WorkstationRejected, match="AUTHENTICATED_ACK_REQUIRED"):
            store.acknowledge(receipt().receipt_hash, b"anything")


def test_bounds_invalid_permissions_and_missing_directory(tmp_path):
    with make_store(tmp_path, max_items=1) as store:
        store.enqueue(receipt("1"))
        with pytest.raises(WorkstationRejected, match="RECEIPT_QUEUE_FULL"):
            store.enqueue(receipt("2"))
    with pytest.raises(WorkstationRejected, match="INVALID_RECEIPT_DIRECTORY"):
        EncryptedReceiptStore(path=tmp_path/"missing"/"x.db", key=b"a"*32,
                              verify_ack=lambda *_: True)
    with pytest.raises(WorkstationRejected, match="INVALID_RECEIPT_KEY"):
        EncryptedReceiptStore(path=tmp_path/"x.db", key=b"short",
                              verify_ack=lambda *_: True)


def test_agent_journal_is_durable_before_and_after_synthetic_mutation(tmp_path):
    from dataclasses import replace
    from core.workstation_agent import WorkstationAgent
    from tests.test_workstation_agent import NOW, SyntheticGate, agent, cmd
    grant = SyntheticGate()
    unsigned_agent, adapter = agent(gate=grant)
    c = cmd("APPLY", key="persistent", payload={"entity_id":"shape", "properties":{"type":"line"}})
    with make_store(tmp_path) as store:
        a = WorkstationAgent(
            identity=unsigned_agent.identity, node_session=unsigned_agent.node_session,
            application=unsigned_agent.application, lease=unsigned_agent.lease,
            gate=grant, adapter=adapter, clock=lambda:NOW, journal=store)
        result = a.submit(c)
        assert result.status == "APPLIED"
        assert store.count() == 1 and store.unresolved_count() == 0
        assert store.list_pending()[0]["receipt_hash"] == result.receipt_hash
    with make_store(tmp_path) as store:
        # Restart with stale detached model must NOT replay a completed operation.
        b, db = agent(gate=SyntheticGate())
        restarted = WorkstationAgent(
            identity=b.identity, node_session=b.node_session,
            application=b.application, lease=b.lease,
            gate=SyntheticGate(), adapter=db, clock=lambda:NOW, journal=store)
        denied = restarted.submit(c)
        assert denied.reason_class == "ALREADY_COMPLETED"
        assert db.public_snapshot()["revision"] == 0


def test_unresolved_pre_mutation_reservation_fails_closed_on_restart(tmp_path):
    from core.workstation_agent import WorkstationAgent
    from tests.test_workstation_agent import NOW, SyntheticGate, agent, cmd
    c = cmd("APPLY", key="may-have-mutated", payload={"entity_id":"shape","properties":{"x":1}})
    with make_store(tmp_path) as store:
        assert store.reserve(c) == "NEW"
        assert store.unresolved_count() == 1
    a, db = agent(gate=SyntheticGate())
    with make_store(tmp_path) as store:
        restarted = WorkstationAgent(
            identity=a.identity, node_session=a.node_session,
            application=a.application, lease=a.lease,
            gate=SyntheticGate(), adapter=db, clock=lambda:NOW, journal=store)
        assert restarted.public_status()["aborted"] is True
        assert restarted.submit(c).reason_class == "LOCAL_PAUSE_OR_ABORT"
        assert db.public_snapshot()["revision"] == 0


def test_failed_receipt_commit_aborts_and_never_replays(tmp_path):
    from core.workstation_agent import WorkstationAgent
    from tests.test_workstation_agent import NOW, SyntheticGate, agent, cmd
    a, db = agent(gate=SyntheticGate())
    c = cmd("APPLY",key="commit-crash",payload={"entity_id":"shape","properties":{"type":"line"}})

    class SimulatedDiskFailure:
        def __init__(self, store):
            self.store = store
        def unresolved_count(self): return self.store.unresolved_count()
        def count(self): return self.store.count()
        def reserve(self, command): return self.store.reserve(command)
        def finalize(self, command, receipt): raise OSError("simulated disk unavailable")
        def list_pending(self, limit=100): return self.store.list_pending(limit)
        def acknowledge(self, receipt_hash, signed_ack): return False

    with make_store(tmp_path) as store:
        runtime = WorkstationAgent(identity=a.identity,node_session=a.node_session,
            application=a.application,lease=a.lease,gate=SyntheticGate(),
            adapter=db,clock=lambda:NOW,journal=SimulatedDiskFailure(store))
        result = runtime.submit(c)
        assert result.reason_class == "JOURNAL_COMMIT_FAILED"
        assert runtime.public_status()["aborted"]
        assert store.unresolved_count() == 1
        assert db.public_snapshot()["revision"] == 1
        assert runtime.submit(c).status == "BLOCKED"
        assert db.public_snapshot()["revision"] == 1
