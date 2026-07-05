from __future__ import annotations

import sqlite3

import pytest

from core.runner_lease_store import RunnerLeaseStore, RunnerLeaseStoreError


SHA = "2eb9e054ee14ce6ebbe535dcd193e9a79bc58a78"
RECEIPT = "a" * 64


def take(store, key="task-1521", token="lease-1", now=100.0, ttl=30.0):
    return store.acquire(
        idempotency_key=key,
        task_reference="#1521",
        repo="alanua/Skeleton",
        branch="runner/issue-1521",
        base_sha=SHA,
        lease_seconds=ttl,
        now=now,
        lease_token=token,
    )


def reason(error, code):
    assert error.value.reason_code == code


def test_persists_and_blocks_parallel_acquire(tmp_path):
    db = tmp_path / "leases.db"
    first = RunnerLeaseStore(db)
    second = RunnerLeaseStore(db)
    try:
        record = take(first)
        assert (record.attempt, record.status, record.expires_at) == (1, "leased", 130.0)
        with pytest.raises(RunnerLeaseStoreError) as error:
            take(second, token="lease-2", now=101.0)
        reason(error, "LEASE_CONFLICT")
    finally:
        first.close()
        second.close()
    with RunnerLeaseStore(db) as reopened:
        assert reopened.get_latest("task-1521").lease_token == "lease-1"


def test_expired_and_failed_attempts_can_retry(tmp_path):
    db = tmp_path / "leases.db"
    with RunnerLeaseStore(db) as store:
        take(store, ttl=10.0)
        retry = take(store, token="lease-2", now=111.0)
        assert retry.attempt == 2
        store.fail("task-1521", "lease-2", "executor failed", now=112.0)
        assert take(store, token="lease-3", now=113.0).attempt == 3
    connection = sqlite3.connect(db)
    try:
        assert connection.execute(
            "SELECT status FROM runner_leases ORDER BY attempt"
        ).fetchall() == [("abandoned",), ("failed",), ("leased",)]
    finally:
        connection.close()


def test_completed_key_cannot_replay(tmp_path):
    with RunnerLeaseStore(tmp_path / "leases.db") as store:
        take(store)
        result = store.complete("task-1521", "lease-1", RECEIPT, now=101.0)
        assert result.status == "completed"
        assert result.receipt_hash == RECEIPT
        with pytest.raises(RunnerLeaseStoreError) as error:
            take(store, token="lease-2", now=102.0)
        reason(error, "COMPLETED_REPLAY_BLOCKED")


def test_updates_are_lease_bound(tmp_path):
    with RunnerLeaseStore(tmp_path / "leases.db") as store:
        take(store)
        with pytest.raises(RunnerLeaseStoreError) as error:
            store.heartbeat("task-1521", "other-lease", 30.0, now=101.0)
        reason(error, "LEASE_TOKEN_MISMATCH")
        assert store.heartbeat("task-1521", "lease-1", 60.0, now=101.0).expires_at == 161.0
        assert store.set_running_pid("task-1521", "lease-1", 4242, now=102.0).pid == 4242
        saved = store.save_checkpoint(
            "task-1521", "lease-1", {"step": "tests", "done": 3}, now=103.0
        )
        assert saved.checkpoint == {"done": 3, "step": "tests"}


def test_expired_update_and_bad_metadata_fail_closed(tmp_path):
    with RunnerLeaseStore(tmp_path / "leases.db") as store:
        take(store, ttl=10.0)
        with pytest.raises(RunnerLeaseStoreError) as expired:
            store.set_running_pid("task-1521", "lease-1", 42, now=111.0)
        reason(expired, "LEASE_EXPIRED")
        with pytest.raises(RunnerLeaseStoreError) as pid:
            store.set_running_pid("task-1521", "lease-1", 0, now=101.0)
        reason(pid, "INVALID_PID")
        with pytest.raises(RunnerLeaseStoreError) as checkpoint:
            store.save_checkpoint("task-1521", "lease-1", {"bad": object()}, now=101.0)
        reason(checkpoint, "INVALID_CHECKPOINT")
        with pytest.raises(RunnerLeaseStoreError) as receipt:
            store.complete("task-1521", "lease-1", "bad", now=101.0)
        reason(receipt, "INVALID_RECEIPT_HASH")


def test_reconcile_only_abandons_local_stale_rows(tmp_path):
    db = tmp_path / "leases.db"
    with RunnerLeaseStore(db) as store:
        take(store, key="keep", token="keep-token", ttl=100.0)
        take(store, key="missing", token="missing-token", ttl=100.0)
        take(store, key="expired", token="expired-token", ttl=5.0)
        assert store.reconcile(("keep", "expired"), now=106.0) == ("expired", "missing")
        assert tuple(row.idempotency_key for row in store.list_active(now=106.0)) == ("keep",)
        assert store.get_latest("unknown") is None
    connection = sqlite3.connect(db)
    try:
        assert connection.execute("SELECT COUNT(*) FROM runner_leases").fetchone()[0] == 3
    finally:
        connection.close()


def test_schema_mismatch_and_closed_store_fail_closed(tmp_path):
    db = tmp_path / "leases.db"
    store = RunnerLeaseStore(db)
    store.close()
    with pytest.raises(RunnerLeaseStoreError) as closed:
        store.get_latest("task")
    reason(closed, "STORE_CLOSED")

    connection = sqlite3.connect(db)
    try:
        connection.execute(
            "UPDATE runner_lease_meta SET value='999' WHERE key='schema_version'"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RunnerLeaseStoreError) as schema:
        RunnerLeaseStore(db)
    reason(schema, "SCHEMA_VERSION_MISMATCH")



def test_idempotency_key_cannot_change_task_identity(tmp_path):
    with RunnerLeaseStore(tmp_path / "leases.db") as store:
        take(store)

        with pytest.raises(RunnerLeaseStoreError) as error:
            store.acquire(
                idempotency_key="task-1521",
                task_reference="#9999",
                repo="alanua/Skeleton",
                branch="runner/other",
                base_sha=SHA,
                lease_seconds=30.0,
                now=200.0,
                lease_token="lease-2",
            )

        reason(error, "IDEMPOTENCY_METADATA_MISMATCH")
