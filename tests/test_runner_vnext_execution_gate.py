from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_adapters import AdapterManifest, AdapterPlanner
from core.runner_vnext_contracts import EffectClass, OperationIR, PrivacyClass, Reversibility, UniversalTask
from core.runner_vnext_control_plane import GreenControlPlane
from core.runner_vnext_environment import LaneEnvironmentPolicy, LaneEnvironmentPolicyRegistry
from core.runner_vnext_execution_gate import ExecutionGateError, GreenExecutionGate, planner_envelope_hash
from core.runner_vnext_leases import Lane, LaneLeaseStore
from core.runner_vnext_ledger import OperationIdentity, OperationLedger
from core.runner_vnext_pep import PEPError, PolicyEnforcementPoint
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot, RoutePlanner
from core.runner_vnext_scheduler import LaneRequest, VNextScheduler

ROOT = Path(__file__).resolve().parents[1]
NOW = 100.0


def task() -> UniversalTask:
    return UniversalTask(
        task_id="task:1", intent="write", domain="github", target_resources=("repo:core/a.py",),
        required_capabilities=("repository_write_allowlisted",), privacy=PrivacyClass.PUBLIC_SAFE,
        reversibility=Reversibility.REVERSIBLE, expected_effects=("workspace_write",),
        validation=("pytest",), rollback=("git:reset",), idempotency_key="idem:1",
    )


def operation() -> OperationIR:
    return OperationIR("op:1", "workspace_write", ("repo:core/a.py",), ("workspace_write",), "idem:1")


def snapshot(*, generation=3, expires=200.0, attestation=None) -> NodeCapabilitySnapshot:
    return NodeCapabilitySnapshot(
        node_id="node:runner-1", generation=generation, route_rank=1,
        capabilities=("repository_write_allowlisted",), supported_adapters=("adapter:repo",),
        supported_lanes=(Lane.CODEGEN,), privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
        resource_patterns=("repo:*",), observed_at=90.0, expires_at=expires,
        attestation_ref=attestation or f"attestation:runner-1-{generation}",
    )


def env_registry(*, inherit=("HOME", "PATH")) -> LaneEnvironmentPolicyRegistry:
    return LaneEnvironmentPolicyRegistry({
        "lane:codegen": LaneEnvironmentPolicy(
            "lane:codegen", inherit, (), ("OPENROUTER_API_KEY", "BWS_ACCESS_TOKEN", "SKELETON_TG_BOT"), ()
        )
    })


def build_stack():
    nodes = NodeCapabilityRegistry(); nodes.register(snapshot(), now=NOW)
    envs = env_registry()
    store = LaneLeaseStore(clock=lambda: NOW)
    scheduler = VNextScheduler(store)
    ledger = OperationLedger()
    manifest = AdapterManifest(
        adapter_id="adapter:repo", operation_kinds=("workspace_write",),
        capabilities=("repository_write_allowlisted",), allowed_effect_classes=(EffectClass.GREEN,),
        resource_patterns=("repo:*",), privacy_classes=(PrivacyClass.PUBLIC_SAFE,), privileged_pep_required=False,
    )
    control = GreenControlPlane(
        route_planner=RoutePlanner(nodes), environment_registry=envs, scheduler=scheduler,
        adapter_planner=AdapterPlanner({"adapter:repo": manifest}), ledger=ledger,
    )
    handoff = control.prepare(
        task=task(), operation=operation(), adapter_id="adapter:repo", lane=Lane.CODEGEN,
        lease_scope_key="repo:branch-main", target_state_ref="git:abc", ttl_seconds=30, now=NOW,
        parent_environment={"HOME":"/home/agent", "PATH":"/usr/bin", "OPENROUTER_API_KEY":"parent-secret"},
        injected_environment={},
    )
    return nodes, envs, store, scheduler, ledger, control, handoff


class TargetVerifier:
    def __init__(self, ref="git:abc") -> None: self.ref = ref
    def current_ref(self, handoff) -> str: return self.ref


class RejectingPEP(PolicyEnforcementPoint):
    def evaluate(self, envelope, ledger, **kwargs): raise PEPError("PEP_GATE_REJECTED")


def gate(nodes, envs, scheduler, ledger, *, verifier=None, pep=None):
    return GreenExecutionGate(
        nodes=nodes, environment_registry=envs, scheduler=scheduler, ledger=ledger,
        target_state_verifier=verifier or TargetVerifier(), pep=pep,
    )


def test_successful_gate_revalidates_exact_state_and_grants_without_starting_effect() -> None:
    nodes, envs, _, scheduler, ledger, _, handoff = build_stack()
    grant = gate(nodes, envs, scheduler, ledger).grant(handoff, now=NOW)
    assert handoff.envelope.dry_run is True
    assert grant.execution_authorized is True and grant.effect_execution_started is False
    assert grant.planner_envelope_hash == planner_envelope_hash(handoff.envelope)
    assert grant.node_snapshot_hash == handoff.node_snapshot_hash
    assert "parent-secret" not in repr(grant)


def test_newer_node_generation_invalidates_old_handoff() -> None:
    nodes, envs, _, scheduler, ledger, _, handoff = build_stack()
    nodes.register(snapshot(generation=4, attestation="attestation:runner-1-4"), now=NOW)
    with pytest.raises(ExecutionGateError, match="NODE_GENERATION_MISMATCH"):
        gate(nodes, envs, scheduler, ledger).grant(handoff, now=NOW)


def test_expired_node_snapshot_cannot_grant() -> None:
    nodes, envs, _, scheduler, ledger, _, handoff = build_stack()
    with pytest.raises(ExecutionGateError, match="NODE_SNAPSHOT_EXPIRED"):
        gate(nodes, envs, scheduler, ledger).grant(handoff, now=201.0)


def test_environment_policy_change_invalidates_handoff() -> None:
    nodes, _, _, scheduler, ledger, _, handoff = build_stack()
    changed_envs = env_registry(inherit=("HOME",))
    with pytest.raises(ExecutionGateError, match="EXECUTION_GATE_ENV_POLICY_MISMATCH"):
        gate(nodes, changed_envs, scheduler, ledger).grant(handoff, now=NOW)


def test_missing_or_changed_lease_fails_closed() -> None:
    nodes, envs, _, scheduler, ledger, _, handoff = build_stack()
    request = LaneRequest("task:1", Lane.CODEGEN, "node:runner-1", "repo:branch-main", 30, "git:abc")
    scheduler.release(request, fence_token=handoff.fence_token)
    with pytest.raises(ExecutionGateError, match="EXECUTION_GATE_LEASE_MISSING_OR_EXPIRED"):
        gate(nodes, envs, scheduler, ledger).grant(handoff, now=NOW)


def test_terminal_ledger_cannot_be_granted_again() -> None:
    nodes, envs, _, scheduler, ledger, _, handoff = build_stack()
    ident = OperationIdentity("op:1", "idem:1", "git:abc")
    ledger.finish(ident, fence_token=handoff.fence_token, success=True, reason_code="VALIDATION_PASS",
                  touched_resource_refs=("repo:core/a.py",), before_state_ref="git:abc",
                  after_state_ref="git:def", validation_status="PASS")
    with pytest.raises(ExecutionGateError, match="EXECUTION_GATE_LEDGER_NOT_RESERVED"):
        gate(nodes, envs, scheduler, ledger).grant(handoff, now=NOW)


def test_target_state_is_reread_at_effect_boundary() -> None:
    nodes, envs, _, scheduler, ledger, _, handoff = build_stack()
    with pytest.raises(ExecutionGateError, match="EXECUTION_GATE_TARGET_STATE_STALE"):
        gate(nodes, envs, scheduler, ledger, verifier=TargetVerifier("git:changed")).grant(handoff, now=NOW)


def test_pep_is_rechecked_at_grant_boundary() -> None:
    nodes, envs, _, scheduler, ledger, _, handoff = build_stack()
    with pytest.raises(ExecutionGateError, match="PEP_GATE_REJECTED"):
        gate(nodes, envs, scheduler, ledger, pep=RejectingPEP()).grant(handoff, now=NOW)


def test_attestation_binding_and_scope_hash_cannot_be_forged() -> None:
    nodes, envs, _, scheduler, ledger, _, handoff = build_stack()
    forged = replace(handoff, node_attestation_ref="attestation:other")
    with pytest.raises(ExecutionGateError, match="EXECUTION_GATE_NODE_ATTESTATION_MISMATCH"):
        gate(nodes, envs, scheduler, ledger).grant(forged, now=NOW)
    widened_envelope = replace(handoff.envelope, resources=("repo:core/a.py", "repo:core/b.py"))
    forged_scope = replace(handoff, envelope=widened_envelope)
    with pytest.raises(ExecutionGateError, match="EXECUTION_GATE_SCOPE_HASH_MISMATCH"):
        gate(nodes, envs, scheduler, ledger).grant(forged_scope, now=NOW)


def test_public_grant_projection_is_closed_value_free_and_has_no_command_surface() -> None:
    nodes, envs, _, scheduler, ledger, _, handoff = build_stack()
    g = gate(nodes, envs, scheduler, ledger)
    grant = g.grant(handoff, now=NOW)
    public = g.public_projection(grant)
    blob = json.dumps(public)
    assert "parent-secret" not in blob
    assert "environment" not in public and "planner_envelope" not in public
    assert "target_state_ref" not in public
    assert not any(key in public for key in ("shell", "argv", "command"))
    schema = json.loads((ROOT / "schemas" / "runner_green_execution_grant.schema.json").read_text())
    jsonschema.validate(public, schema)
