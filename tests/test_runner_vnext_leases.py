from __future__ import annotations

import pytest

from core.runner_vnext_leases import Lane, LaneLeaseStore, LeaseError


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
