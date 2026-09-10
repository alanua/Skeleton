from __future__ import annotations

from dataclasses import dataclass

from core.runner_vnext_leases import Lane, LaneLeaseStore, LeaseReceipt


@dataclass(frozen=True)
class LaneRequest:
    task_id: str
    lane: Lane
    owner: str
    scope_key: str
    ttl_seconds: float
    target_state_ref: str | None


class VNextScheduler:
    def __init__(self, lease_store: LaneLeaseStore) -> None:
        self._lease_store = lease_store

    def reserve(self, request: LaneRequest) -> LeaseReceipt:
        return self._lease_store.acquire(
            task_id=request.task_id,
            lane=request.lane,
            owner=request.owner,
            scope_key=request.scope_key,
            ttl_seconds=request.ttl_seconds,
            target_state_ref=request.target_state_ref,
        )


    def release(self, request: LaneRequest, *, fence_token: int) -> LeaseReceipt:
        return self._lease_store.release(
            lane=request.lane,
            scope_key=request.scope_key,
            owner=request.owner,
            fence_token=fence_token,
        )

    def current(self, *, lane: Lane, scope_key: str):
        return self._lease_store.current(lane=lane, scope_key=scope_key)

    @staticmethod
    def public_projection(receipt: LeaseReceipt) -> dict[str, object]:
        return {
            "schema": "skeleton.runner_lane_projection.v1",
            "status": receipt.status,
            "reason_code": receipt.reason_code,
            "lane": receipt.lane.value,
            "fence_token": receipt.fence_token,
        }
