from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_contracts import PrivacyClass, Reversibility, UniversalTask
from core.runner_vnext_leases import Lane
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot, RoutePlanner, RoutingError, node_capability_snapshot_hash

ROOT = Path(__file__).resolve().parents[1]
NOW = 100.0


def task(*, privacy=PrivacyClass.PUBLIC_SAFE, capabilities=("repository_write_allowlisted",), resources=("repo:core/a.py",)) -> UniversalTask:
    return UniversalTask(
        task_id="task:1",
        intent="write",
        domain="github",
        target_resources=resources,
        required_capabilities=capabilities,
        privacy=privacy,
        reversibility=Reversibility.REVERSIBLE,
        expected_effects=("workspace_write",),
        validation=("pytest",),
        rollback=("git:reset",),
        idempotency_key="idem:1",
    )


def node(
    node_id="node:runner-1",
    *,
    generation=1,
    route_rank=10,
    capabilities=("repository_write_allowlisted",),
    adapters=("adapter:repo",),
    lanes=(Lane.CODEGEN,),
    privacy=(PrivacyClass.PUBLIC_SAFE,),
    patterns=("repo:*",),
    observed=90.0,
    expires=200.0,
) -> NodeCapabilitySnapshot:
    return NodeCapabilitySnapshot(
        node_id=node_id,
        generation=generation,
        route_rank=route_rank,
        capabilities=capabilities,
        supported_adapters=adapters,
        supported_lanes=lanes,
        privacy_classes=privacy,
        resource_patterns=patterns,
        observed_at=observed,
        expires_at=expires,
        attestation_ref=f"attestation:{node_id.split(':', 1)[1]}-{generation}",
    )


def planner(*snapshots: NodeCapabilitySnapshot) -> RoutePlanner:
    registry = NodeCapabilityRegistry()
    for snapshot in snapshots:
        registry.register(snapshot, now=NOW)
    return RoutePlanner(registry)


def test_routes_only_when_capability_adapter_lane_privacy_and_resources_match() -> None:
    plan = planner(node()).plan(task=task(), adapter_id="adapter:repo", lane=Lane.CODEGEN, now=NOW)
    assert plan.status == "ROUTED"
    assert plan.selected_node_id == "node:runner-1"
    assert plan.selected_generation == 1
    assert plan.selected_snapshot_hash == node_capability_snapshot_hash(node())
    assert plan.selected_attestation_ref == "attestation:runner-1-1"
    assert plan.selected_expires_at == 200.0
    assert plan.execution_authorized is False and plan.lease_acquired is False


def test_private_task_requires_explicit_private_node_support() -> None:
    public = node()
    p = planner(public)
    plan = p.plan(task=task(privacy=PrivacyClass.PRIVATE, resources=("private:artifact-1",)), adapter_id="adapter:repo", lane=Lane.CODEGEN, now=NOW)
    assert plan.status == "NO_ROUTE" and plan.reason_code == "NO_ROUTE_PRIVACY"
    private = node(node_id="node:private-1", privacy=(PrivacyClass.PRIVATE,), patterns=("private:*",))
    plan = planner(public, private).plan(task=task(privacy=PrivacyClass.PRIVATE, resources=("private:artifact-1",)), adapter_id="adapter:repo", lane=Lane.CODEGEN, now=NOW)
    assert plan.selected_node_id == "node:private-1"


def test_capability_adapter_lane_and_resource_mismatches_are_typed_no_route() -> None:
    base = node()
    cases = [
        (task(capabilities=("cad_export",)), "adapter:repo", Lane.CODEGEN, "NO_ROUTE_CAPABILITY"),
        (task(), "adapter:cad", Lane.CODEGEN, "NO_ROUTE_ADAPTER"),
        (task(), "adapter:repo", Lane.VALIDATE, "NO_ROUTE_LANE"),
        (task(resources=("mail:message-1",)), "adapter:repo", Lane.CODEGEN, "NO_ROUTE_RESOURCE"),
    ]
    p = planner(base)
    for t, adapter, lane, reason in cases:
        plan = p.plan(task=t, adapter_id=adapter, lane=lane, now=NOW)
        assert plan.status == "NO_ROUTE" and plan.reason_code == reason


def test_expired_snapshot_is_rejected_and_future_expiry_causes_no_route() -> None:
    registry = NodeCapabilityRegistry()
    with pytest.raises(RoutingError, match="NODE_SNAPSHOT_EXPIRED"):
        registry.register(node(expires=99.0), now=NOW)
    registry.register(node(expires=101.0), now=NOW)
    plan = RoutePlanner(registry).plan(task=task(), adapter_id="adapter:repo", lane=Lane.CODEGEN, now=102.0)
    assert plan.reason_code == "NO_ROUTE_NO_FRESH_NODE"


def test_generation_rollback_and_same_generation_content_change_fail_closed() -> None:
    registry = NodeCapabilityRegistry()
    registry.register(node(generation=2), now=NOW)
    with pytest.raises(RoutingError, match="NODE_GENERATION_ROLLBACK"):
        registry.register(node(generation=1), now=NOW)
    with pytest.raises(RoutingError, match="NODE_GENERATION_CONTENT_MISMATCH"):
        registry.register(node(generation=2, route_rank=99), now=NOW)


def test_deterministic_ranking_uses_explicit_rank_then_node_id() -> None:
    p = planner(
        node(node_id="node:z", route_rank=5),
        node(node_id="node:b", route_rank=1),
        node(node_id="node:a", route_rank=1),
    )
    plan = p.plan(task=task(), adapter_id="adapter:repo", lane=Lane.CODEGEN, now=NOW)
    assert plan.selected_node_id == "node:a"
    assert plan.candidate_node_ids == ("node:a", "node:b", "node:z")


def test_global_wildcard_private_path_and_malformed_refs_fail_closed() -> None:
    registry = NodeCapabilityRegistry()
    with pytest.raises(RoutingError, match="NODE_RESOURCE_PATTERN_INVALID"):
        registry.register(node(patterns=("*:*",)), now=NOW)
    with pytest.raises(RoutingError, match="NODE_ATTESTATION_REF_INVALID"):
        bad = node()
        registry.register(NodeCapabilitySnapshot(**{**bad.__dict__, "attestation_ref": "/private/attestation"}), now=NOW)
    with pytest.raises(RoutingError, match="ROUTE_TARGET_RESOURCE_INVALID"):
        planner(node()).plan(task=task(resources=("repo:/private/file",)), adapter_id="adapter:repo", lane=Lane.CODEGEN, now=NOW)
    bad_task = task()
    bad_task = UniversalTask(**{**bad_task.__dict__, "task_id": "/private/task"})
    with pytest.raises(RoutingError, match="ROUTE_TASK_ID_INVALID"):
        planner(node()).plan(task=bad_task, adapter_id="adapter:repo", lane=Lane.CODEGEN, now=NOW)
    with pytest.raises(RoutingError, match="NODE_ID_INVALID"):
        registry.register(node(node_id="node:/private"), now=NOW)


def test_route_plan_contains_only_public_safe_selection_metadata() -> None:
    plan = planner(node()).plan(task=task(), adapter_id="adapter:repo", lane=Lane.CODEGEN, now=NOW)
    payload = asdict(plan)
    assert "target_resources" not in payload
    assert "capabilities" not in payload
    assert payload["execution_authorized"] is False


def test_closed_schemas_validate_public_safe_snapshots_and_plans() -> None:
    snapshot_schema = json.loads((ROOT / "schemas" / "runner_node_capability_snapshot.schema.json").read_text())
    plan_schema = json.loads((ROOT / "schemas" / "runner_route_plan.schema.json").read_text())
    assert snapshot_schema["additionalProperties"] is False
    assert plan_schema["additionalProperties"] is False
    snapshot = node()
    snapshot_payload = {
        "schema": "skeleton.runner_node_capability_snapshot.v1",
        "node_id": snapshot.node_id,
        "generation": snapshot.generation,
        "route_rank": snapshot.route_rank,
        "capabilities": list(snapshot.capabilities),
        "supported_adapters": list(snapshot.supported_adapters),
        "supported_lanes": [lane.value for lane in snapshot.supported_lanes],
        "privacy_classes": [p.value for p in snapshot.privacy_classes],
        "resource_patterns": list(snapshot.resource_patterns),
        "observed_at": snapshot.observed_at,
        "expires_at": snapshot.expires_at,
        "attestation_ref": snapshot.attestation_ref,
    }
    jsonschema.validate(snapshot_payload, snapshot_schema)
    plan = planner(snapshot).plan(task=task(), adapter_id="adapter:repo", lane=Lane.CODEGEN, now=NOW)
    plan_payload = {
        "schema": "skeleton.runner_route_plan.v1",
        **{k: (v.value if isinstance(v, Lane) else list(v) if isinstance(v, tuple) else v) for k, v in asdict(plan).items()},
    }
    jsonschema.validate(plan_payload, plan_schema)


def test_snapshot_hash_changes_when_any_routing_authority_field_changes() -> None:
    base = node()
    changed = node(route_rank=11)
    assert node_capability_snapshot_hash(base) != node_capability_snapshot_hash(changed)
