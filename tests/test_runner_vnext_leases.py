from __future__ import annotations

import pytest

from core.runner_vnext_leases import Lane, LaneLeaseStore, LeaseError
from core.runner_vnext_ledger import LedgerError, OperationIdentity, OperationLedger


SCOPE_HASH = "a" * 64


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_acquire_conflict_and_idempotent_same_owner() -> None:
    clock = Clock()
    store = LaneLeaseStore(clock=clock)
    first = store.acquire(task_id="t1", lane=Lane.VALIDATE, owner="w1", scope_key="repo", ttl_seconds=30, target_state_ref="sha:a")
    again = store.acquire(task_id="t1", lane=Lane.VALIDATE, owner="w1", scope_key="repo", ttl_seconds=30, target_state_ref="sha:a")
    assert first.reason_code == "LEASE_ACQUIRED"
    assert again.reason_code == "LEASE_ALREADY_HELD"
    assert again.fence_token == first.fence_token
    with pytest.raises(LeaseError, match="LEASE_CONFLICT_ACTIVE_OWNER"):
        store.acquire(task_id="t2", lane=Lane.VALIDATE, owner="w2", scope_key="repo", ttl_seconds=30, target_state_ref="sha:a")


def test_expired_lease_reclaim_increments_fence_and_rejects_stale_writer() -> None:
    clock = Clock()
    store = LaneLeaseStore(clock=clock)
    first = store.acquire(task_id="t1", lane=Lane.PUBLISH, owner="w1", scope_key="branch:x", ttl_seconds=10, target_state_ref="sha:a")
    clock.now = 111.0
    second = store.acquire(task_id="t2", lane=Lane.PUBLISH, owner="w2", scope_key="branch:x", ttl_seconds=10, target_state_ref="sha:b")
    assert second.reason_code == "STALE_LEASE_RECLAIMED"
    assert second.fence_token == first.fence_token + 1
    with pytest.raises(LeaseError, match="STALE_FENCE_TOKEN"):
        store.heartbeat(lane=Lane.PUBLISH, scope_key="branch:x", owner="w1", fence_token=first.fence_token)


def test_expired_owner_cannot_mutate_retained_newer_lease() -> None:
    clock = Clock()
    store = LaneLeaseStore(clock=clock)
    first = store.acquire(task_id="t1", lane=Lane.CODEGEN, owner="w1", scope_key="repo", ttl_seconds=10, target_state_ref="git:old")
    clock.now = 111.0
    second = store.acquire(task_id="t2", lane=Lane.CODEGEN, owner="w2", scope_key="repo", ttl_seconds=10, target_state_ref="git:new")

    with pytest.raises(LeaseError, match="STALE_FENCE_TOKEN"):
        store.release(lane=Lane.CODEGEN, scope_key="repo", owner="w1", fence_token=first.fence_token)
    with pytest.raises(LeaseError, match="LEASE_OWNER_MISMATCH"):
        store.heartbeat(lane=Lane.CODEGEN, scope_key="repo", owner="w1", fence_token=second.fence_token)
    with pytest.raises(LeaseError, match="LEASE_CONFLICT_ACTIVE_OWNER"):
        store.acquire(task_id="t1", lane=Lane.CODEGEN, owner="w1", scope_key="repo", ttl_seconds=10, target_state_ref="git:old")

    current = store.current(lane=Lane.CODEGEN, scope_key="repo")
    assert current is not None
    assert current.task_id == "t2"
    assert current.owner == "w2"
    assert current.fence_token == second.fence_token
    assert current.target_state_ref == "git:new"


def test_stale_interrupted_task_cannot_finish_rebound_operation_or_newer_task() -> None:
    clock = Clock()
    lease_store = LaneLeaseStore(clock=clock)
    ledger = OperationLedger()
    stale = OperationIdentity("op:old", "idem:old", "git:old")
    newer = OperationIdentity("op:new", "idem:new", "git:new")

    first = lease_store.acquire(task_id="task:old", lane=Lane.CODEGEN, owner="node:old", scope_key="repo", ttl_seconds=5, target_state_ref="git:old")
    ledger.reserve(stale, fence_token=first.fence_token, reservation_scope_hash=SCOPE_HASH)
    clock.now = 106.0
    second = lease_store.acquire(task_id="task:new", lane=Lane.CODEGEN, owner="node:new", scope_key="repo", ttl_seconds=5, target_state_ref="git:new")
    ledger.rebind_fence(stale, expected_fence_token=first.fence_token, new_fence_token=second.fence_token)
    ledger.reserve(newer, fence_token=second.fence_token, reservation_scope_hash=SCOPE_HASH)

    with pytest.raises(LedgerError, match="STALE_FENCE_TOKEN"):
        ledger.finish(stale, fence_token=first.fence_token, success=True, reason_code="PASS", touched_resource_refs=("repo:file.py",), before_state_ref="git:old", after_state_ref="git:old-done", validation_status="PASS")
    with pytest.raises(LedgerError, match="IDEMPOTENCY_TARGET_STATE_MISMATCH"):
        ledger.finish(OperationIdentity("op:old", "idem:new", "git:old"), fence_token=second.fence_token, success=True, reason_code="PASS", touched_resource_refs=("repo:file.py",), before_state_ref="git:new", after_state_ref="git:new-done", validation_status="PASS")

    assert ledger.status("idem:old") == "RESERVED"
    assert ledger.status("idem:new") == "RESERVED"
    assert lease_store.current(lane=Lane.CODEGEN, scope_key="repo").task_id == "task:new"


def test_heartbeat_extends_live_lease_but_not_expired_one() -> None:
    clock = Clock()
    store = LaneLeaseStore(clock=clock)
    receipt = store.acquire(task_id="t1", lane=Lane.CODEGEN, owner="w1", scope_key="repo", ttl_seconds=10, target_state_ref=None)
    clock.now = 105.0
    beat = store.heartbeat(lane=Lane.CODEGEN, scope_key="repo", owner="w1", fence_token=receipt.fence_token)
    assert beat.reason_code == "LEASE_HEARTBEAT_RECORDED"
    clock.now = 116.0
    with pytest.raises(LeaseError, match="LEASE_EXPIRED"):
        store.heartbeat(lane=Lane.CODEGEN, scope_key="repo", owner="w1", fence_token=receipt.fence_token)


def test_merge_lane_can_be_global_single_writer() -> None:
    clock = Clock()
    store = LaneLeaseStore(clock=clock)
    store.acquire(task_id="m1", lane=Lane.MERGE, owner="w1", scope_key="global", ttl_seconds=30, target_state_ref="pr:1@sha")
    with pytest.raises(LeaseError, match="LEASE_CONFLICT_ACTIVE_OWNER"):
        store.acquire(task_id="m2", lane=Lane.MERGE, owner="w2", scope_key="global", ttl_seconds=30, target_state_ref="pr:2@sha")


def test_publish_lanes_fence_per_branch_scope() -> None:
    clock = Clock()
    store = LaneLeaseStore(clock=clock)
    a = store.acquire(task_id="p1", lane=Lane.PUBLISH, owner="w1", scope_key="branch:a", ttl_seconds=30, target_state_ref="sha:a")
    b = store.acquire(task_id="p2", lane=Lane.PUBLISH, owner="w2", scope_key="branch:b", ttl_seconds=30, target_state_ref="sha:b")
    assert a.fence_token == 1
    assert b.fence_token == 1


def test_sqlite_lease_and_fence_survive_process_reopen(tmp_path) -> None:
    clock = Clock()
    db = tmp_path / "leases.sqlite"
    store = LaneLeaseStore(db, clock=clock)
    first = store.acquire(task_id="t1", lane=Lane.CONTROL, owner="w1", scope_key="control", ttl_seconds=10, target_state_ref="sha:a")
    store.close()

    clock.now = 111.0
    reopened = LaneLeaseStore(db, clock=clock)
    second = reopened.acquire(task_id="t2", lane=Lane.CONTROL, owner="w2", scope_key="control", ttl_seconds=10, target_state_ref="sha:b")
    assert second.reason_code == "STALE_LEASE_RECLAIMED"
    assert second.fence_token == first.fence_token + 1
    reopened.close()


def test_same_holder_cannot_reuse_lease_for_different_target_state() -> None:
    clock = Clock()
    store = LaneLeaseStore(clock=clock)
    store.acquire(task_id="t1", lane=Lane.PUBLISH, owner="w1", scope_key="branch:x", ttl_seconds=30, target_state_ref="sha:a")
    with pytest.raises(LeaseError, match="LEASE_TARGET_STATE_MISMATCH"):
        store.acquire(task_id="t1", lane=Lane.PUBLISH, owner="w1", scope_key="branch:x", ttl_seconds=30, target_state_ref="sha:b")


def test_expired_holder_cannot_release_stale_lease() -> None:
    clock = Clock()
    store = LaneLeaseStore(clock=clock)
    receipt = store.acquire(task_id="t1", lane=Lane.CONTROL, owner="w1", scope_key="control", ttl_seconds=5, target_state_ref="sha:a")
    clock.now = 106.0
    with pytest.raises(LeaseError, match="LEASE_EXPIRED"):
        store.release(lane=Lane.CONTROL, scope_key="control", owner="w1", fence_token=receipt.fence_token)


def test_store_supports_serialized_multi_thread_use(tmp_path) -> None:
    import threading

    clock = Clock()
    store = LaneLeaseStore(tmp_path / "leases.sqlite", clock=clock)
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            receipt = store.acquire(task_id=f"t{index}", lane=Lane.VALIDATE, owner=f"w{index}", scope_key=f"repo:{index}", ttl_seconds=30, target_state_ref=f"sha:{index}")
            store.heartbeat(lane=Lane.VALIDATE, scope_key=f"repo:{index}", owner=f"w{index}", fence_token=receipt.fence_token)
        except Exception as exc:  # pragma: no cover - assertion reports collected errors
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
