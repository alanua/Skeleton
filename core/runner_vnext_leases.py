from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable


class Lane(str, Enum):
    CODEGEN = "codegen"
    VALIDATE = "validate"
    PUBLISH = "publish"
    MERGE = "merge"
    CONTROL = "control"
    PRIVILEGED = "privileged"


class LeaseError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LaneLease:
    task_id: str
    lane: Lane
    owner: str
    fence_token: int
    acquired_at: float
    heartbeat_at: float
    ttl_seconds: float
    target_state_ref: str | None
    scope_key: str

    def expired(self, now: float) -> bool:
        return now >= self.heartbeat_at + self.ttl_seconds


@dataclass(frozen=True)
class LeaseReceipt:
    status: str
    reason_code: str
    task_id: str
    lane: Lane
    scope_key: str
    fence_token: int


class LaneLeaseStore:
    """Authoritative in-process contract store for vNext lease semantics.

    Persistence is intentionally outside this slice; callers must not derive authority
    from GitHub labels or process liveness.
    """

    def __init__(self, *, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._leases: dict[tuple[Lane, str], LaneLease] = {}
        self._next_fence: dict[tuple[Lane, str], int] = {}

    def _key(self, lane: Lane, scope_key: str) -> tuple[Lane, str]:
        return lane, scope_key

    def acquire(
        self,
        *,
        task_id: str,
        lane: Lane,
        owner: str,
        scope_key: str,
        ttl_seconds: float,
        target_state_ref: str | None,
    ) -> LeaseReceipt:
        if ttl_seconds <= 0:
            raise LeaseError("INVALID_LEASE_TTL")
        now = self._clock()
        key = self._key(lane, scope_key)
        current = self._leases.get(key)
        if current is not None and not current.expired(now):
            if current.task_id == task_id and current.owner == owner:
                return LeaseReceipt("ALREADY_HELD", "LEASE_ALREADY_HELD", task_id, lane, scope_key, current.fence_token)
            raise LeaseError("LEASE_CONFLICT_ACTIVE_OWNER")
        fence = self._next_fence.get(key, 0) + 1
        self._next_fence[key] = fence
        self._leases[key] = LaneLease(task_id, lane, owner, fence, now, now, ttl_seconds, target_state_ref, scope_key)
        reason = "STALE_LEASE_RECLAIMED" if current is not None else "LEASE_ACQUIRED"
        return LeaseReceipt("ACQUIRED", reason, task_id, lane, scope_key, fence)

    def heartbeat(self, *, lane: Lane, scope_key: str, owner: str, fence_token: int) -> LeaseReceipt:
        now = self._clock()
        key = self._key(lane, scope_key)
        current = self._leases.get(key)
        if current is None:
            raise LeaseError("LEASE_NOT_FOUND")
        if current.fence_token != fence_token:
            raise LeaseError("STALE_FENCE_TOKEN")
        if current.owner != owner:
            raise LeaseError("LEASE_OWNER_MISMATCH")
        if current.expired(now):
            raise LeaseError("LEASE_EXPIRED")
        self._leases[key] = replace(current, heartbeat_at=now)
        return LeaseReceipt("HEARTBEAT", "LEASE_HEARTBEAT_RECORDED", current.task_id, lane, scope_key, fence_token)

    def release(self, *, lane: Lane, scope_key: str, owner: str, fence_token: int) -> LeaseReceipt:
        key = self._key(lane, scope_key)
        current = self._leases.get(key)
        if current is None:
            raise LeaseError("LEASE_NOT_FOUND")
        if current.fence_token != fence_token:
            raise LeaseError("STALE_FENCE_TOKEN")
        if current.owner != owner:
            raise LeaseError("LEASE_OWNER_MISMATCH")
        del self._leases[key]
        return LeaseReceipt("RELEASED", "LEASE_RELEASED", current.task_id, lane, scope_key, fence_token)

    def current(self, *, lane: Lane, scope_key: str) -> LaneLease | None:
        current = self._leases.get(self._key(lane, scope_key))
        if current is None or current.expired(self._clock()):
            return None
        return current
