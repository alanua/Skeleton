from __future__ import annotations

import pytest

from core.runner_vnext_adapters import AdapterContractError, AdapterManifest, AdapterPlanner
from core.runner_vnext_contracts import (EffectClass, OperationIR, PolicyDecision, PolicyInput, PrivacyClass, Reversibility, UniversalTask, classify_policy)


def manifest(**overrides) -> AdapterManifest:
    values = dict(adapter_id="repo", operation_kinds=("workspace_write",), capabilities=("repository_write_allowlisted",), allowed_effect_classes=(EffectClass.GREEN,), resource_patterns=("repo:*",), privacy_classes=(PrivacyClass.PUBLIC_SAFE,), privileged_pep_required=False)
    values.update(overrides)
    return AdapterManifest(**values)


def operation(resource="repo:core/example.py") -> OperationIR:
    return OperationIR("op1", "workspace_write", (resource,), ("workspace_write",), "idem1")



def task(op=None, privacy=PrivacyClass.PUBLIC_SAFE, capabilities=("repository_write_allowlisted",)) -> UniversalTask:
    op = op or operation()
    return UniversalTask("task1", "test", "runner", op.resources, capabilities, privacy, Reversibility.REVERSIBLE, op.effects, ("test",), ("rollback",), op.idempotency_key)


def policy(op=None, privacy=PrivacyClass.PUBLIC_SAFE, state="git:abc") -> PolicyInput:
    return PolicyInput(op or operation(), Reversibility.REVERSIBLE, privacy, state)

def decision(effect=EffectClass.GREEN, privileged=False) -> PolicyDecision:
    return PolicyDecision(effect, "GREEN_AUTONOMOUS_TYPED_EFFECT" if effect is EffectClass.GREEN else "YELLOW_EFFECT_BOUNDARY", privileged)


def test_green_envelope_is_dry_run_and_exact_state_bound() -> None:
    planner = AdapterPlanner({"repo": manifest()})
    envelope = planner.plan(adapter_id="repo", task=task(), operation=operation(), policy_input=policy(), decision=classify_policy(policy()), target_state_ref="git:abc", fence_token=7)
    assert envelope.dry_run is True
    assert envelope.target_state_ref == "git:abc"
    assert envelope.fence_token == 7


def test_unknown_adapter_and_capability_fail_closed() -> None:
    planner = AdapterPlanner({"repo": manifest()})
    with pytest.raises(AdapterContractError, match="ADAPTER_UNKNOWN"):
        planner.plan(adapter_id="missing", task=task(), operation=operation(), policy_input=policy(state="git:a"), decision=classify_policy(policy(state="git:a")), target_state_ref="git:a", fence_token=1)
    with pytest.raises(AdapterContractError, match="ADAPTER_CAPABILITY_UNSUPPORTED"):
        planner.plan(adapter_id="repo", task=task(capabilities=("root",)), operation=operation(), policy_input=policy(state="git:a"), decision=classify_policy(policy(state="git:a")), target_state_ref="git:a", fence_token=1)


def test_resource_and_privacy_scope_fail_closed() -> None:
    planner = AdapterPlanner({"repo": manifest(allowed_effect_classes=(EffectClass.GREEN, EffectClass.YELLOW), privileged_pep_required=True)})
    with pytest.raises(AdapterContractError, match="ADAPTER_RESOURCE_OUT_OF_SCOPE"):
        planner.plan(adapter_id="repo", task=task(operation("protected:core/action_gate.py")), operation=operation("protected:core/action_gate.py"), policy_input=policy(operation("protected:core/action_gate.py"), state="git:a"), decision=classify_policy(policy(operation("protected:core/action_gate.py"), state="git:a")), target_state_ref="git:a", fence_token=1)
    with pytest.raises(AdapterContractError, match="ADAPTER_PRIVACY_UNSUPPORTED"):
        planner.plan(adapter_id="repo", task=task(privacy=PrivacyClass.PRIVATE), operation=operation(), policy_input=policy(privacy=PrivacyClass.PRIVATE, state="git:a"), decision=classify_policy(policy(privacy=PrivacyClass.PRIVATE, state="git:a")), target_state_ref="git:a", fence_token=1)


def test_yellow_requires_manifest_and_privileged_pep_boundary() -> None:
    op = operation("protected:core/action_gate.py")
    pi = policy(op, state="git:a")
    yellow = classify_policy(pi)
    with pytest.raises(AdapterContractError, match="ADAPTER_EFFECT_CLASS_UNSUPPORTED"):
        AdapterPlanner({"repo": manifest(resource_patterns=("protected:*",))}).plan(adapter_id="repo", task=task(op), operation=op, policy_input=pi, decision=yellow, target_state_ref="git:a", fence_token=1)
    m = manifest(allowed_effect_classes=(EffectClass.GREEN, EffectClass.YELLOW), resource_patterns=("protected:*",), privileged_pep_required=True)
    envelope = AdapterPlanner({"repo": m}).plan(adapter_id="repo", task=task(op), operation=op, policy_input=pi, decision=yellow, target_state_ref="git:a", fence_token=1)
    assert envelope.effect_class is EffectClass.YELLOW

def test_red_manifest_without_privileged_pep_is_rejected() -> None:
    op = operation("secret:opaque-ref")
    pi = policy(op, state="git:a")
    red = classify_policy(pi)
    bad = manifest(allowed_effect_classes=(EffectClass.RED,), resource_patterns=("secret:*",), privileged_pep_required=False)
    with pytest.raises(AdapterContractError, match="ADAPTER_RED_REQUIRES_PRIVILEGED_PEP"):
        AdapterPlanner({"repo": bad}).plan(adapter_id="repo", task=task(op), operation=op, policy_input=pi, decision=red, target_state_ref="git:a", fence_token=1)

def test_invalid_target_state_and_fence_fail_closed() -> None:
    planner = AdapterPlanner({"repo": manifest()})
    for state, fence, reason in [("",1,"ADAPTER_TARGET_STATE_REF_INVALID"),("/private",1,"ADAPTER_TARGET_STATE_REF_INVALID"),("git:a",0,"ADAPTER_FENCE_INVALID")]:
        with pytest.raises(AdapterContractError, match=reason):
            planner.plan(adapter_id="repo", task=task(), operation=operation(), policy_input=policy(state=state), decision=classify_policy(policy(state=state)), target_state_ref=state, fence_token=fence)


def test_forged_or_stale_policy_decision_fails_closed() -> None:
    op = operation()
    pi = policy(op, state="git:a")
    forged = PolicyDecision(EffectClass.YELLOW, "YELLOW_EFFECT_BOUNDARY", True)
    with pytest.raises(AdapterContractError, match="ADAPTER_POLICY_DECISION_MISMATCH"):
        AdapterPlanner({"repo": manifest(allowed_effect_classes=(EffectClass.GREEN, EffectClass.YELLOW), privileged_pep_required=True)}).plan(adapter_id="repo", task=task(op), operation=op, policy_input=pi, decision=forged, target_state_ref="git:a", fence_token=1)


def test_task_policy_and_operation_bindings_fail_closed() -> None:
    op = operation()
    pi = policy(op, state="git:a")
    good = classify_policy(pi)
    planner = AdapterPlanner({"repo": manifest()})
    with pytest.raises(AdapterContractError, match="ADAPTER_POLICY_TARGET_STATE_MISMATCH"):
        planner.plan(adapter_id="repo", task=task(op), operation=op, policy_input=pi, decision=good, target_state_ref="git:b", fence_token=1)
    other = operation("repo:core/other.py")
    with pytest.raises(AdapterContractError, match="ADAPTER_TASK_OPERATION_SCOPE_MISMATCH"):
        planner.plan(adapter_id="repo", task=task(other), operation=op, policy_input=pi, decision=good, target_state_ref="git:a", fence_token=1)


def test_manifest_rejects_global_or_namespace_wildcard_resource_patterns() -> None:
    for pattern in ("*", "*:*", "*:core/*", "/private/*", "repo:../*"):
        with pytest.raises(AdapterContractError, match="ADAPTER_RESOURCE_PATTERN_INVALID"):
            AdapterPlanner({"repo": manifest(resource_patterns=(pattern,))})


def test_target_state_must_be_public_safe_namespaced_ref() -> None:
    planner = AdapterPlanner({"repo": manifest()})
    for state in ("abc", "git:abc def", "git:../abc", "\\private"):
        with pytest.raises(AdapterContractError, match="ADAPTER_TARGET_STATE_REF_INVALID"):
            planner.plan(adapter_id="repo", task=task(), operation=operation(), policy_input=policy(state=state), decision=classify_policy(policy(state=state)), target_state_ref=state, fence_token=1)
