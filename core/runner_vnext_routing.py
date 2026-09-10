from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
import hashlib
import json
from typing import Iterable

from core.runner_vnext_contracts import PrivacyClass, UniversalTask
from core.runner_vnext_leases import Lane


class RoutingError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class NodeCapabilitySnapshot:
    node_id: str
    generation: int
    route_rank: int
    capabilities: tuple[str, ...]
    supported_adapters: tuple[str, ...]
    supported_lanes: tuple[Lane, ...]
    privacy_classes: tuple[PrivacyClass, ...]
    resource_patterns: tuple[str, ...]
    observed_at: float
    expires_at: float
    attestation_ref: str


@dataclass(frozen=True)
class RoutePlan:
    task_id: str
    status: str
    reason_code: str
    adapter_id: str
    lane: Lane
    selected_node_id: str | None
    selected_generation: int | None
    selected_snapshot_hash: str | None
    selected_attestation_ref: str | None
    selected_expires_at: float | None
    candidate_node_ids: tuple[str, ...]
    lease_acquired: bool = False
    execution_authorized: bool = False


class NodeCapabilityRegistry:
    """Authoritative typed capability snapshots for pure route planning."""

    def __init__(self) -> None:
        self._snapshots: dict[str, NodeCapabilitySnapshot] = {}

    def register(self, snapshot: NodeCapabilitySnapshot, *, now: float) -> None:
        _validate_snapshot(snapshot, now=now)
        existing = self._snapshots.get(snapshot.node_id)
        if existing is not None:
            if snapshot.generation < existing.generation:
                raise RoutingError("NODE_GENERATION_ROLLBACK")
            if snapshot.generation == existing.generation:
                if snapshot != existing:
                    raise RoutingError("NODE_GENERATION_CONTENT_MISMATCH")
                return
        self._snapshots[snapshot.node_id] = snapshot

    def active(self, *, now: float) -> tuple[NodeCapabilitySnapshot, ...]:
        return tuple(
            sorted(
                (s for s in self._snapshots.values() if s.observed_at <= now < s.expires_at),
                key=lambda s: (s.route_rank, s.node_id),
            )
        )


class RoutePlanner:
    def __init__(self, registry: NodeCapabilityRegistry) -> None:
        self._registry = registry

    def plan(self, *, task: UniversalTask, adapter_id: str, lane: Lane, now: float) -> RoutePlan:
        if not _public_ref(task.task_id) or not task.task_id.startswith("task:"):
            raise RoutingError("ROUTE_TASK_ID_INVALID")
        if not _public_ref(adapter_id):
            raise RoutingError("ROUTE_ADAPTER_ID_INVALID")
        if not task.required_capabilities:
            raise RoutingError("ROUTE_CAPABILITY_REQUIREMENT_REQUIRED")
        if not task.target_resources:
            raise RoutingError("ROUTE_TARGET_RESOURCE_REQUIRED")
        for resource in task.target_resources:
            if not _public_ref(resource):
                raise RoutingError("ROUTE_TARGET_RESOURCE_INVALID")

        fresh = self._registry.active(now=now)
        if not fresh:
            return _no_route(task, adapter_id, lane, "NO_ROUTE_NO_FRESH_NODE")

        by_capability = tuple(s for s in fresh if set(task.required_capabilities).issubset(set(s.capabilities)))
        if not by_capability:
            return _no_route(task, adapter_id, lane, "NO_ROUTE_CAPABILITY")

        by_adapter = tuple(s for s in by_capability if adapter_id in s.supported_adapters)
        if not by_adapter:
            return _no_route(task, adapter_id, lane, "NO_ROUTE_ADAPTER")

        by_lane = tuple(s for s in by_adapter if lane in s.supported_lanes)
        if not by_lane:
            return _no_route(task, adapter_id, lane, "NO_ROUTE_LANE")

        by_privacy = tuple(s for s in by_lane if task.privacy in s.privacy_classes)
        if not by_privacy:
            return _no_route(task, adapter_id, lane, "NO_ROUTE_PRIVACY")

        by_resource = tuple(s for s in by_privacy if _all_resources_match(task.target_resources, s.resource_patterns))
        if not by_resource:
            return _no_route(task, adapter_id, lane, "NO_ROUTE_RESOURCE")

        ordered = tuple(sorted(by_resource, key=lambda s: (s.route_rank, s.node_id)))
        selected = ordered[0]
        return RoutePlan(
            task_id=task.task_id,
            status="ROUTED",
            reason_code="ROUTE_MATCHED_TYPED_CAPABILITIES",
            adapter_id=adapter_id,
            lane=lane,
            selected_node_id=selected.node_id,
            selected_generation=selected.generation,
            selected_snapshot_hash=node_capability_snapshot_hash(selected),
            selected_attestation_ref=selected.attestation_ref,
            selected_expires_at=selected.expires_at,
            candidate_node_ids=tuple(s.node_id for s in ordered),
        )


def _no_route(task: UniversalTask, adapter_id: str, lane: Lane, reason_code: str) -> RoutePlan:
    return RoutePlan(
        task_id=task.task_id,
        status="NO_ROUTE",
        reason_code=reason_code,
        adapter_id=adapter_id,
        lane=lane,
        selected_node_id=None,
        selected_generation=None,
        selected_snapshot_hash=None,
        selected_attestation_ref=None,
        selected_expires_at=None,
        candidate_node_ids=(),
    )


def _validate_snapshot(snapshot: NodeCapabilitySnapshot, *, now: float) -> None:
    if not _node_id(snapshot.node_id):
        raise RoutingError("NODE_ID_INVALID")
    if snapshot.generation < 1:
        raise RoutingError("NODE_GENERATION_INVALID")
    if snapshot.route_rank < 0:
        raise RoutingError("NODE_ROUTE_RANK_INVALID")
    if snapshot.observed_at > now:
        raise RoutingError("NODE_SNAPSHOT_FROM_FUTURE")
    if snapshot.expires_at <= snapshot.observed_at:
        raise RoutingError("NODE_SNAPSHOT_TIME_RANGE_INVALID")
    if snapshot.expires_at <= now:
        raise RoutingError("NODE_SNAPSHOT_EXPIRED")
    if not _public_ref(snapshot.attestation_ref):
        raise RoutingError("NODE_ATTESTATION_REF_INVALID")
    _nonempty_unique(snapshot.capabilities, "NODE_CAPABILITY_DECLARATION_REQUIRED")
    _nonempty_unique(snapshot.supported_adapters, "NODE_ADAPTER_DECLARATION_REQUIRED")
    _nonempty_unique(snapshot.supported_lanes, "NODE_LANE_DECLARATION_REQUIRED")
    _nonempty_unique(snapshot.privacy_classes, "NODE_PRIVACY_DECLARATION_REQUIRED")
    _nonempty_unique(snapshot.resource_patterns, "NODE_RESOURCE_DECLARATION_REQUIRED")
    for capability in snapshot.capabilities:
        if not _token(capability):
            raise RoutingError("NODE_CAPABILITY_INVALID")
    for adapter in snapshot.supported_adapters:
        if not _public_ref(adapter):
            raise RoutingError("NODE_ADAPTER_ID_INVALID")
    for pattern in snapshot.resource_patterns:
        _validate_resource_pattern(pattern)


def _nonempty_unique(values: Iterable[object], reason_code: str) -> None:
    items = tuple(values)
    if not items or len(set(items)) != len(items):
        raise RoutingError(reason_code)


def _all_resources_match(resources: tuple[str, ...], patterns: tuple[str, ...]) -> bool:
    return all(any(fnmatch(resource, pattern) for pattern in patterns) for resource in resources)


def _validate_resource_pattern(pattern: str) -> None:
    if not _public_ref(pattern) or pattern in {"*", "*:*"}:
        raise RoutingError("NODE_RESOURCE_PATTERN_INVALID")
    namespace, _, tail = pattern.partition(":")
    if not namespace or any(ch in namespace for ch in "*?[]") or not tail:
        raise RoutingError("NODE_RESOURCE_PATTERN_INVALID")


def _public_ref(value: str) -> bool:
    if not value or "\\" in value or ".." in value or any(ch.isspace() for ch in value):
        return False
    namespace, sep, tail = value.partition(":")
    return bool(sep and namespace and tail) and not tail.startswith(("/", "~"))


def _node_id(value: str) -> bool:
    if not value.startswith("node:") or not _public_ref(value):
        return False
    return all(ch.isalnum() or ch in "._-" for ch in value[5:])


def _token(value: str) -> bool:
    return bool(value) and not any(ch.isspace() for ch in value) and ":" not in value and "/" not in value and "\\" not in value


def node_capability_snapshot_hash(snapshot: NodeCapabilitySnapshot) -> str:
    payload = {
        "node_id": snapshot.node_id,
        "generation": snapshot.generation,
        "route_rank": snapshot.route_rank,
        "capabilities": list(snapshot.capabilities),
        "supported_adapters": list(snapshot.supported_adapters),
        "supported_lanes": [lane.value for lane in snapshot.supported_lanes],
        "privacy_classes": [privacy.value for privacy in snapshot.privacy_classes],
        "resource_patterns": list(snapshot.resource_patterns),
        "observed_at": snapshot.observed_at,
        "expires_at": snapshot.expires_at,
        "attestation_ref": snapshot.attestation_ref,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
