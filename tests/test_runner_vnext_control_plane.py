from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_adapters import AdapterManifest, AdapterPlanner
from core.runner_vnext_contracts import EffectClass, OperationIR, PrivacyClass, Reversibility, UniversalTask
from core.runner_vnext_control_plane import ControlPlaneError, GreenControlPlane
from core.runner_vnext_environment import LaneEnvironmentPolicy, LaneEnvironmentPolicyRegistry
from core.runner_vnext_leases import Lane, LaneLeaseStore
from core.runner_vnext_ledger import OperationIdentity, OperationLedger
from core.runner_vnext_pep import PEPError, PolicyEnforcementPoint, reservation_scope_fingerprint
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot, RoutePlanner
from core.runner_vnext_scheduler import VNextScheduler

ROOT = Path(__file__).resolve().parents[1]
NOW = 100.0


def task(*, resources=("repo:core/a.py",), effects=("workspace_write",)) -> UniversalTask:
    return UniversalTask(
        task_id="task:1", intent="write", domain="github", target_resources=resources,
        required_capabilities=("repository_write_allowlisted",), privacy=PrivacyClass.PUBLIC_SAFE,
        reversibility=Reversibility.REVERSIBLE, expected_effects=effects,
        validation=("pytest",), rollback=("git:reset",), idempotency_key="idem:1",
    )


def operation(*, resources=("repo:core/a.py",), effects=("workspace_write",)) -> OperationIR:
    return OperationIR("op:1", "workspace_write", resources, effects, "idem:1")


def build_control(*, node_patterns=("repo:*",), adapter_patterns=("repo:*",), pep=None):
    nodes = NodeCapabilityRegistry()
    nodes.register(NodeCapabilitySnapshot(
        node_id="node:runner-1", generation=3, route_rank=1,
        capabilities=("repository_write_allowlisted",), supported_adapters=("adapter:repo",),
        supported_lanes=(Lane.CODEGEN,), privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
        resource_patterns=node_patterns, observed_at=90.0, expires_at=200.0,
        attestation_ref="attestation:runner-1-3",
    ), now=NOW)
    envs = LaneEnvironmentPolicyRegistry({
        "lane:codegen": LaneEnvironmentPolicy("lane:codegen", ("HOME", "PATH"), (), ("OPENROUTER_API_KEY", "BWS_ACCESS_TOKEN", "SKELETON_TG_BOT"), ()),
    })
    manifest = AdapterManifest(
        adapter_id="adapter:repo", operation_kinds=("workspace_write",),
        capabilities=("repository_write_allowlisted",), allowed_effect_classes=(EffectClass.GREEN,),
        resource_patterns=adapter_patterns, privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
        privileged_pep_required=False,
    )
    store = LaneLeaseStore(clock=lambda: NOW)
    ledger = OperationLedger()
    control = GreenControlPlane(
        route_planner=RoutePlanner(nodes), environment_registry=envs,
        scheduler=VNextScheduler(store), adapter_planner=AdapterPlanner({"adapter:repo": manifest}),
        ledger=ledger, pep=pep,
    )
    return control, store, ledger


def prepare(control: GreenControlPlane, *, t=None, op=None, injected=None):
    return control.prepare(
        task=t or task(), operation=op or operation(), adapter_id="adapter:repo", lane=Lane.CODEGEN,
        lease_scope_key="repo:branch-main", target_state_ref="git:abc", ttl_seconds=30, now=NOW,
        parent_environment={"HOME":"/home/agent", "PATH":"/usr/bin", "OPENROUTER_API_KEY":"parent-secret"},
        injected_environment=injected or {},
    )


def test_successful_green_preparation_retains_exact_lease_and_reservation() -> None:
    control, store, ledger = build_control()
    handoff = prepare(control)
    current = store.current(lane=Lane.CODEGEN, scope_key="repo:branch-main")
    assert current is not None and current.fence_token == handoff.fence_token
    assert current.owner == "node:runner-1"
    assert ledger.status("idem:1") == "RESERVED"
    reservation = ledger.reservation_event("idem:1")
    assert reservation.reservation_scope_hash == handoff.reservation_scope_hash
    assert handoff.execution_authorized is True and handoff.side_effects_executed is False
    assert "parent-secret" not in repr(handoff)


def test_no_route_stops_before_lease_or_ledger() -> None:
    control, store, ledger = build_control(node_patterns=("mail:*",))
    with pytest.raises(ControlPlaneError, match="NO_ROUTE_RESOURCE"):
        prepare(control)
    assert store.current(lane=Lane.CODEGEN, scope_key="repo:branch-main") is None
    assert ledger.status("idem:1") is None


def test_environment_failure_stops_before_lease_or_ledger() -> None:
    control, store, ledger = build_control()
    with pytest.raises(ControlPlaneError, match="ENV_DENIED_INJECTION"):
        prepare(control, injected={"OPENROUTER_API_KEY":"secret"})
    assert store.current(lane=Lane.CODEGEN, scope_key="repo:branch-main") is None
    assert ledger.status("idem:1") is None


def test_adapter_failure_releases_acquired_lease() -> None:
    control, store, ledger = build_control(adapter_patterns=("mail:*",))
    with pytest.raises(ControlPlaneError, match="ADAPTER_RESOURCE_OUT_OF_SCOPE"):
        prepare(control)
    assert store.current(lane=Lane.CODEGEN, scope_key="repo:branch-main") is None
    assert ledger.status("idem:1") is None


def test_conflicting_ledger_scope_releases_lease_and_cannot_handoff() -> None:
    control, store, ledger = build_control()
    ident = OperationIdentity("op:1", "idem:1", "git:abc")
    ledger.reserve(ident, fence_token=1, reservation_scope_hash="a" * 64)
    with pytest.raises(ControlPlaneError, match="IDEMPOTENCY_SCOPE_HASH_MISMATCH"):
        prepare(control)
    assert store.current(lane=Lane.CODEGEN, scope_key="repo:branch-main") is None
    assert ledger.status("idem:1") == "RESERVED"


class RejectingPEP(PolicyEnforcementPoint):
    def evaluate(self, envelope, ledger, **kwargs):
        raise PEPError("PEP_TEST_REJECTION")


def test_pep_failure_yields_no_handoff_releases_lease_and_terminally_accounts_new_reservation() -> None:
    control, store, ledger = build_control(pep=RejectingPEP())
    with pytest.raises(ControlPlaneError, match="PEP_TEST_REJECTION"):
        prepare(control)
    assert store.current(lane=Lane.CODEGEN, scope_key="repo:branch-main") is None
    assert ledger.status("idem:1") == "FAILED"
    assert ledger.history("idem:1")[-1].reason_code == "CONTROL_PLANE_PREPARATION_FAILED"


def test_yellow_operation_cannot_enter_green_handoff_or_acquire_lease() -> None:
    control, store, ledger = build_control()
    t = task(effects=("external_send",))
    op = operation(effects=("external_send",))
    with pytest.raises(ControlPlaneError, match="CONTROL_PLANE_PRIVILEGED_PATH_REQUIRED"):
        prepare(control, t=t, op=op)
    assert store.current(lane=Lane.CODEGEN, scope_key="repo:branch-main") is None
    assert ledger.status("idem:1") is None


def test_public_projection_is_value_free_and_schema_valid() -> None:
    control, _, _ = build_control()
    handoff = prepare(control)
    public = control.public_projection(handoff)
    blob = json.dumps(public)
    assert "parent-secret" not in blob
    assert "environment" not in public and "lease_scope_key" not in public
    schema = json.loads((ROOT / "schemas" / "runner_green_handoff_receipt.schema.json").read_text())
    jsonschema.validate(public, schema)


def test_task_operation_mismatch_stops_before_lease_or_ledger() -> None:
    control, store, ledger = build_control()
    mismatch = operation(resources=("repo:core/b.py",))
    with pytest.raises(ControlPlaneError, match="CONTROL_PLANE_TASK_OPERATION_RESOURCE_MISMATCH"):
        prepare(control, op=mismatch)
    assert store.current(lane=Lane.CODEGEN, scope_key="repo:branch-main") is None
    assert ledger.status("idem:1") is None


def test_failed_idempotent_retry_does_not_release_preexisting_lease() -> None:
    control, store, ledger = build_control()
    first = prepare(control)
    ident = OperationIdentity("op:1", "idem:1", "git:abc")
    ledger.finish(ident, fence_token=first.fence_token, success=True, reason_code="VALIDATION_PASS",
                  touched_resource_refs=("repo:core/a.py",), before_state_ref="git:abc",
                  after_state_ref="git:def", validation_status="PASS")
    with pytest.raises(ControlPlaneError, match="CONTROL_PLANE_OPERATION_TERMINAL"):
        prepare(control)
    current = store.current(lane=Lane.CODEGEN, scope_key="repo:branch-main")
    assert current is not None and current.fence_token == first.fence_token
