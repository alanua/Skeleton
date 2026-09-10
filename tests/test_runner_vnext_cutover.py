from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_adapters import AdapterManifest, AdapterPlanner
from core.runner_vnext_contracts import EffectClass, OperationIR, PrivacyClass, Reversibility, UniversalTask
from core.runner_vnext_control_plane import GreenControlPlane
from core.runner_vnext_cutover import (
    CutoverEvidence,
    CutoverPolicy,
    CutoverReadinessError,
    CutoverReadinessEvaluator,
    FailureInjectionEvidence,
    ValidationEvidence,
)
from core.runner_vnext_environment import LaneEnvironmentPolicy, LaneEnvironmentPolicyRegistry
from core.runner_vnext_execution import GreenExecutionError, GreenExecutionLifecycle, GreenExecutionResult, execution_grant_hash
from core.runner_vnext_execution_gate import GreenExecutionGate
from core.runner_vnext_leases import Lane, LaneLeaseStore
from core.runner_vnext_ledger import OperationLedger
from core.runner_vnext_reconciliation import OutcomeEvidence, StartedOperationReconciler
from core.runner_vnext_routing import NodeCapabilityRegistry, NodeCapabilitySnapshot, RoutePlanner
from core.runner_vnext_scheduler import VNextScheduler
from core.runner_vnext_shadow import DivergenceKind, ShadowParityReceipt

ROOT = Path(__file__).resolve().parents[1]
NOW = 100.0
STACK_HEADS = (
    "github-pr:3912@b79983f",
    "github-pr:3915@889eb29",
    "github-pr:3917@3de2733",
    "github-pr:3919@91cff37",
    "github-pr:3923@aae7c34",
    "github-pr:3925@8a8a550",
    "github-pr:3927@d171481",
    "github-pr:3929@2fe9def",
    "github-pr:3931@cac7ac4",
    "github-pr:3933@d3e43c7",
    "github-pr:3935@3ad26c3",
    "github-pr:3937@46854bd",
    "github-pr:3940@25e73ad",
    "github-pr:3942@47afb23",
)


def policy(*, allow_incomparable: bool = False) -> CutoverPolicy:
    return CutoverPolicy(
        minimum_parity_observations=3,
        required_validation_profiles=("vnext-full", "pycompile", "diff-check"),
        required_failure_cases=("crash-after-start", "stale-fence", "partial-rollback", "private-redaction"),
        allow_incomparable=allow_incomparable,
    )


def parity(kind: DivergenceKind, index: int = 1) -> ShadowParityReceipt:
    legacy = EffectClass.GREEN
    vnext = EffectClass.GREEN
    if kind is DivergenceKind.VNEXT_STRICTER:
        vnext = EffectClass.YELLOW
    elif kind is DivergenceKind.LEGACY_STRICTER:
        legacy = EffectClass.YELLOW
    elif kind is DivergenceKind.INCOMPARABLE:
        legacy = None
    elif kind is DivergenceKind.INVALID_LEGACY_INPUT:
        vnext = None
    return ShadowParityReceipt(
        source_task_ref=f"github-issue:{3900 + index}",
        target_state_ref=f"git:state-{index}",
        legacy_effect_class=legacy,
        legacy_status="DONE",
        vnext_effect_class=vnext,
        vnext_reason_code="GREEN_AUTONOMOUS_TYPED_EFFECT" if vnext is EffectClass.GREEN else "YELLOW_EFFECT_BOUNDARY",
        divergence=kind,
        evidence_refs=(f"github-comment:evidence-{index}",),
    )


def validation(profile: str, *, passed: bool = True, count: int = 1) -> ValidationEvidence:
    return ValidationEvidence(
        profile=profile,
        commit_ref="git:47afb2370312fa4e3011cb7b0acba5eedc55ba0a",
        passed=passed,
        test_count=count,
        evidence_refs=(f"test-report:{profile}",),
    )


def failure(case_id: str, *, passed: bool = True) -> FailureInjectionEvidence:
    return FailureInjectionEvidence(
        case_id=case_id,
        commit_ref="git:47afb2370312fa4e3011cb7b0acba5eedc55ba0a",
        passed=passed,
        evidence_refs=(f"test-report:{case_id}",),
    )


def ready_evidence(*, parity_receipts=None, unresolved=0, privacy=True, rollback_ref="github-issue:3941", blockers=()):
    return CutoverEvidence(
        stack_head_refs=STACK_HEADS,
        parity_receipts=parity_receipts or (
            parity(DivergenceKind.MATCH, 1),
            parity(DivergenceKind.MATCH, 2),
            parity(DivergenceKind.VNEXT_STRICTER, 3),
        ),
        validations=tuple(validation(name) for name in policy().required_validation_profiles),
        failure_cases=tuple(failure(name) for name in policy().required_failure_cases),
        rollback_evidence_ref=rollback_ref,
        unresolved_started_count=unresolved,
        privacy_boundary_verified=privacy,
        blockers=blockers,
    )


def test_complete_evidence_is_ready_only_for_operator_decision() -> None:
    evaluator = CutoverReadinessEvaluator()
    receipt = evaluator.evaluate(policy=policy(), evidence=ready_evidence())
    assert receipt.status == "READY_FOR_OPERATOR_DECISION"
    assert receipt.cutover_authorized is False and receipt.operator_approval_required is True
    assert receipt.parity_match_count == 2 and receipt.parity_vnext_stricter_count == 1
    assert receipt.parity_legacy_stricter_count == 0 and receipt.parity_invalid_count == 0
    assert len(receipt.policy_hash) == 64 and len(receipt.evidence_hash) == 64
    public = evaluator.public_projection(receipt)
    schema = json.loads((ROOT / "schemas" / "runner_cutover_readiness_receipt.schema.json").read_text())
    jsonschema.validate(public, schema)
    assert public["cutover_authorized"] is False


def test_blockers_and_unsafe_parity_prevent_ready() -> None:
    evaluator = CutoverReadinessEvaluator()
    legacy_stricter = ready_evidence(parity_receipts=(
        parity(DivergenceKind.MATCH, 1), parity(DivergenceKind.MATCH, 2), parity(DivergenceKind.LEGACY_STRICTER, 3),
    ))
    receipt = evaluator.evaluate(policy=policy(), evidence=legacy_stricter)
    assert receipt.status == "BLOCKED" and "LEGACY_STRICTER_DIVERGENCE" in receipt.reason_codes

    unresolved = evaluator.evaluate(policy=policy(), evidence=ready_evidence(unresolved=1))
    assert unresolved.status == "BLOCKED" and "UNRESOLVED_STARTED_OPERATIONS" in unresolved.reason_codes

    privacy = evaluator.evaluate(policy=policy(), evidence=ready_evidence(privacy=False))
    assert privacy.status == "BLOCKED" and "PRIVACY_BOUNDARY_NOT_VERIFIED" in privacy.reason_codes

    declared = evaluator.evaluate(policy=policy(), evidence=ready_evidence(blockers=("runtime-parity-gap",)))
    assert declared.status == "BLOCKED" and "DECLARED_BLOCKER_PRESENT" in declared.reason_codes


def test_missing_evidence_is_needs_more_not_ready() -> None:
    evaluator = CutoverReadinessEvaluator()
    too_few = ready_evidence(parity_receipts=(parity(DivergenceKind.MATCH, 1), parity(DivergenceKind.MATCH, 2)))
    receipt = evaluator.evaluate(policy=policy(), evidence=too_few)
    assert receipt.status == "NEEDS_MORE_EVIDENCE"
    assert "PARITY_OBSERVATION_MINIMUM_NOT_MET" in receipt.reason_codes

    incomparable = ready_evidence(parity_receipts=(
        parity(DivergenceKind.MATCH, 1), parity(DivergenceKind.MATCH, 2), parity(DivergenceKind.INCOMPARABLE, 3),
    ))
    receipt = evaluator.evaluate(policy=policy(), evidence=incomparable)
    assert receipt.status == "NEEDS_MORE_EVIDENCE"
    assert "INCOMPARABLE_PARITY_REQUIRES_EVIDENCE" in receipt.reason_codes
    waived = evaluator.evaluate(policy=policy(allow_incomparable=True), evidence=incomparable)
    assert waived.status == "READY_FOR_OPERATOR_DECISION"

    no_rollback = evaluator.evaluate(policy=policy(), evidence=ready_evidence(rollback_ref=None))
    assert no_rollback.status == "NEEDS_MORE_EVIDENCE" and "ROLLBACK_EVIDENCE_MISSING" in no_rollback.reason_codes


def test_failed_required_validation_or_failure_case_blocks() -> None:
    base = ready_evidence()
    validations = list(base.validations)
    validations[0] = validation("vnext-full", passed=False, count=0)
    bad_validation = CutoverEvidence(**{**base.__dict__, "validations": tuple(validations)})
    receipt = CutoverReadinessEvaluator().evaluate(policy=policy(), evidence=bad_validation)
    assert receipt.status == "BLOCKED"
    assert any(code.startswith("VALIDATION_PROFILE_FAILED_") for code in receipt.reason_codes)

    cases = list(base.failure_cases)
    cases[0] = failure("crash-after-start", passed=False)
    bad_case = CutoverEvidence(**{**base.__dict__, "failure_cases": tuple(cases)})
    receipt = CutoverReadinessEvaluator().evaluate(policy=policy(), evidence=bad_case)
    assert receipt.status == "BLOCKED"
    assert any(code.startswith("FAILURE_CASE_FAILED_") for code in receipt.reason_codes)


def test_evidence_shape_rejects_duplicates_unsafe_refs_and_shadow_side_effects() -> None:
    base = ready_evidence()
    duplicate_heads = CutoverEvidence(**{**base.__dict__, "stack_head_refs": (STACK_HEADS[0], STACK_HEADS[0])})
    with pytest.raises(CutoverReadinessError, match="CUTOVER_STACK_HEAD_REF_DUPLICATE"):
        CutoverReadinessEvaluator().evaluate(policy=policy(), evidence=duplicate_heads)

    unsafe = ShadowParityReceipt(
        source_task_ref="/private/task", target_state_ref="git:a", legacy_effect_class=EffectClass.GREEN,
        legacy_status="DONE", vnext_effect_class=EffectClass.GREEN,
        vnext_reason_code="GREEN_AUTONOMOUS_TYPED_EFFECT", divergence=DivergenceKind.MATCH,
        evidence_refs=("github-comment:evidence",),
    )
    bad_parity = CutoverEvidence(**{**base.__dict__, "parity_receipts": (unsafe,)})
    with pytest.raises(CutoverReadinessError, match="CUTOVER_PARITY_REF_INVALID"):
        CutoverReadinessEvaluator().evaluate(policy=policy(), evidence=bad_parity)

    side_effect = ShadowParityReceipt(
        source_task_ref="github-issue:1", target_state_ref="git:a", legacy_effect_class=EffectClass.GREEN,
        legacy_status="DONE", vnext_effect_class=EffectClass.GREEN,
        vnext_reason_code="GREEN_AUTONOMOUS_TYPED_EFFECT", divergence=DivergenceKind.MATCH,
        evidence_refs=("github-comment:evidence",), side_effects_executed=True,
    )
    bad_shadow = CutoverEvidence(**{**base.__dict__, "parity_receipts": (side_effect,)})
    with pytest.raises(CutoverReadinessError, match="CUTOVER_SHADOW_SIDE_EFFECT_DETECTED"):
        CutoverReadinessEvaluator().evaluate(policy=policy(), evidence=bad_shadow)


def task() -> UniversalTask:
    return UniversalTask(
        task_id="task:cutover-e2e", intent="write", domain="github", target_resources=("repo:core/a.py",),
        required_capabilities=("repository_write_allowlisted",), privacy=PrivacyClass.PUBLIC_SAFE,
        reversibility=Reversibility.REVERSIBLE, expected_effects=("workspace_write",),
        validation=("pytest",), rollback=("git:reset",), idempotency_key="idem:cutover-e2e",
    )


def operation() -> OperationIR:
    return OperationIR("op:cutover-e2e", "workspace_write", ("repo:core/a.py",), ("workspace_write",), "idem:cutover-e2e")


class TargetVerifier:
    def current_ref(self, handoff) -> str:
        return "git:abc"


def build_runtime():
    nodes = NodeCapabilityRegistry()
    nodes.register(NodeCapabilitySnapshot(
        node_id="node:runner-e2e", generation=1, route_rank=1,
        capabilities=("repository_write_allowlisted",), supported_adapters=("adapter:repo",),
        supported_lanes=(Lane.CODEGEN,), privacy_classes=(PrivacyClass.PUBLIC_SAFE,), resource_patterns=("repo:*",),
        observed_at=90.0, expires_at=200.0, attestation_ref="attestation:runner-e2e-1",
    ), now=NOW)
    envs = LaneEnvironmentPolicyRegistry({
        "lane:codegen": LaneEnvironmentPolicy(
            "lane:codegen", ("HOME", "PATH"), (),
            ("OPENROUTER_API_KEY", "BWS_ACCESS_TOKEN", "SKELETON_TG_BOT"), (),
        )
    })
    scheduler = VNextScheduler(LaneLeaseStore(clock=lambda: NOW))
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
        lease_scope_key="repo:branch-cutover", target_state_ref="git:abc", ttl_seconds=30, now=NOW,
        parent_environment={"HOME":"/home/agent", "PATH":"/usr/bin", "OPENROUTER_API_KEY":"must-strip"},
        injected_environment={},
    )
    gate = GreenExecutionGate(
        nodes=nodes, environment_registry=envs, scheduler=scheduler, ledger=ledger,
        target_state_verifier=TargetVerifier(),
    )
    grant = gate.grant(handoff, now=NOW)
    return scheduler, ledger, grant


def result_for(grant, *, outcome="SUCCEEDED", after="git:def") -> GreenExecutionResult:
    return GreenExecutionResult(
        operation_id=grant.operation_id, idempotency_key=grant.idempotency_key, adapter_id=grant.adapter_id,
        target_state_ref=grant.target_state_ref, fence_token=grant.fence_token,
        resources=grant.planner_envelope.resources, effects=grant.planner_envelope.effects,
        outcome=outcome, reason_code="EXECUTION_PASS" if outcome == "SUCCEEDED" else "EXECUTION_PARTIAL",
        before_state_ref="git:abc", after_state_ref=after,
        validation_status="PASS" if outcome == "SUCCEEDED" else "FAIL", rollback_requested=False,
    )


class FakeExecutor:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = 0
    def execute(self, grant):
        self.calls += 1
        return self.result


class Probe:
    def __init__(self, evidence) -> None:
        self.evidence = evidence
    def probe(self, grant):
        return self.evidence


class Verifier:
    def verify(self, grant, evidence) -> bool:
        return True


def outcome_evidence(grant, *, outcome="SUCCEEDED", after="git:def") -> OutcomeEvidence:
    return OutcomeEvidence(
        operation_id=grant.operation_id, idempotency_key=grant.idempotency_key, adapter_id=grant.adapter_id,
        target_state_ref=grant.target_state_ref, fence_token=grant.fence_token,
        execution_grant_hash=execution_grant_hash(grant), resources=grant.planner_envelope.resources,
        effects=grant.planner_envelope.effects, outcome=outcome,
        reason_code="RECONCILIATION_PASS" if outcome == "SUCCEEDED" else "RECONCILIATION_PARTIAL",
        before_state_ref="git:abc", after_state_ref=after,
        validation_status="PASS" if outcome == "SUCCEEDED" else "FAIL",
        evidence_refs=("attestation:outcome-e2e",), fresh=True, rollback_requested=False,
    )


def test_end_to_end_green_pipeline_terminalizes_and_releases_lease() -> None:
    scheduler, ledger, grant = build_runtime()
    executor = FakeExecutor(result_for(grant))
    receipt = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).run(grant, executor)
    assert executor.calls == 1 and receipt.terminal_status == "COMPLETED"
    assert ledger.status(grant.idempotency_key) == "COMPLETED"
    assert scheduler.current(lane=Lane.CODEGEN, scope_key=grant.lease_scope_key) is None


def test_failure_injection_crash_after_started_never_reexecutes_and_reconciles() -> None:
    scheduler, ledger, grant = build_runtime()
    lifecycle = GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger)
    lifecycle.begin(grant)
    duplicate = FakeExecutor(result_for(grant))
    with pytest.raises(GreenExecutionError, match="EXECUTION_ALREADY_STARTED_NEEDS_RECOVERY"):
        lifecycle.run(grant, duplicate)
    assert duplicate.calls == 0 and ledger.status(grant.idempotency_key) == "STARTED"
    reconciled = StartedOperationReconciler(scheduler=scheduler, ledger=ledger).reconcile(
        grant, Probe(outcome_evidence(grant)), evidence_verifier=Verifier(),
    )
    assert reconciled.status == "RECONCILED_COMPLETED"
    assert ledger.status(grant.idempotency_key) == "COMPLETED"
    assert reconciled.lease_released is True


def test_failure_injection_partial_outcome_emits_non_executable_rollback() -> None:
    scheduler, ledger, grant = build_runtime()
    GreenExecutionLifecycle(scheduler=scheduler, ledger=ledger).begin(grant)
    evidence = outcome_evidence(grant, outcome="PARTIAL", after="git:partial")
    receipt = StartedOperationReconciler(scheduler=scheduler, ledger=ledger).reconcile(
        grant, Probe(evidence), evidence_verifier=Verifier(),
    )
    assert receipt.status == "RECONCILED_FAILED" and receipt.rollback_required is True
    assert receipt.rollback_request is not None and receipt.rollback_request.execution_authorized is False
    assert receipt.rollback_request.rollback_idempotency_key != grant.idempotency_key


def test_integration_evidence_can_feed_ready_gate_without_authorizing_cutover() -> None:
    evidence = ready_evidence()
    receipt = CutoverReadinessEvaluator().evaluate(policy=policy(), evidence=evidence)
    assert receipt.status == "READY_FOR_OPERATOR_DECISION"
    assert receipt.operator_approval_required is True and receipt.cutover_authorized is False
