from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
from types import MappingProxyType
from typing import Callable, Mapping

from core.runner_task import RunnerTask
from core.runner_vnext_adapters import AdapterManifest, AdapterPlanner, ExecutionEnvelope
from core.runner_vnext_contracts import (
    EffectClass,
    OperationIR,
    PolicyDecision,
    PolicyInput,
    PrivacyClass,
    Reversibility,
    UniversalTask,
    classify_policy,
)
from core.runner_vnext_control_plane import GreenControlPlane, GreenExecutionHandoff
from core.runner_vnext_environment import LaneEnvironmentPolicy, LaneEnvironmentPolicyRegistry
from core.runner_vnext_execution_gate import GreenExecutionGate, GreenExecutionGrant, TargetStateVerifier
from core.runner_vnext_leases import Lane, LaneLeaseStore
from core.runner_vnext_ledger import OperationIdentity, OperationLedger
from core.runner_vnext_pep import (
    AuthorityClaim,
    AuthorityVerifier,
    PEPError,
    PolicyEnforcementPoint,
    PrivilegedBrokerRequest,
    reservation_scope_fingerprint,
)
from core.runner_vnext_routing import NodeCapabilityRegistry, RoutePlanner
from core.runner_vnext_scheduler import VNextScheduler

AUTHORITATIVE_MODE = "authoritative"
ORDINARY_REPOSITORY_PRIVACY = "PUBLIC_SAFE_REPOSITORY_ONLY"
ROUTE_CODE_GENERATION = "code_generation"
ROUTE_VALIDATION = "validation"
ROUTE_PUBLISH_ONLY = "publish_only"
ROUTE_RUNTIME_ONLY = "runtime_only"
ROUTE_RECOVERY = "recovery"
ROUTE_MERGE = "merge"

DISPATCH_TASK_CODEGEN = "codegen"
DISPATCH_TASK_VALIDATION = "validation"
DISPATCH_TASK_PUBLICATION = "publication"
DISPATCH_TASK_CONTROL = "control"
DISPATCH_TASK_RECOVERY = "recovery"
DISPATCH_TASK_MERGE = "merge"


class RunnerVNextAuthorityError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RunnerVNextRuntimeConfig:
    state_root: str
    ledger_db_path: str
    lease_db_path: str


@dataclass(frozen=True)
class RunnerVNextStores:
    ledger: OperationLedger
    lease_store: LaneLeaseStore
    scheduler: VNextScheduler

    def close(self) -> None:
        self.ledger.close()
        self.lease_store.close()


@dataclass(frozen=True)
class RouteAuthorityBinding:
    route: str
    operation: str
    lane: Lane
    adapter_id: str
    operation_kind: str
    effects: tuple[str, ...]
    reversibility: Reversibility
    requires_runner_task: bool
    requires_operator: bool = False
    requires_fresh_operator_evidence: bool = False


@dataclass(frozen=True)
class SourceAuthorityIdentity:
    runner_task_hash: str
    source_task_ref: str
    adapter_id: str
    binding_hash: str
    task_id: str
    operation_id: str
    idempotency_key: str


@dataclass(frozen=True)
class BoundRunnerOperation:
    route: str
    operation: str
    binding: RouteAuthorityBinding
    runner_task: RunnerTask | None
    source_task_ref: str
    runner_task_hash: str | None
    source_binding_hash: str
    universal_task: UniversalTask
    operation_ir: OperationIR
    policy_input: PolicyInput
    policy_decision: PolicyDecision
    target_state_ref: str
    lease_scope_key: str


@dataclass(frozen=True)
class PrivilegedAuthorityInput:
    source_task_ref: str
    target_state_ref: str
    resources: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    privacy: PrivacyClass
    operator_boundary_evidence: tuple[str, ...]
    idempotency_seed: str


@dataclass(frozen=True)
class RunnerVNextAuthorityReceipt:
    mode: str
    status: str
    reason_code: str
    route: str
    operation: str
    lane: str | None
    adapter_id: str | None
    effect_class: str | None
    target_state_ref_hash: str | None
    idempotency_key_hash: str | None
    source_binding_hash: str | None
    execution_authorized: bool = False
    privileged_broker_request: bool = False
    allow_legacy_mechanical_shell: bool = False
    side_effects_executed: bool = False

    def to_public_mapping(self) -> dict[str, object]:
        return {
            "schema": "skeleton.runner_vnext_authority_receipt.v1",
            "mode": self.mode,
            "status": self.status,
            "reason_code": self.reason_code,
            "route": self.route,
            "operation": self.operation,
            "lane": self.lane,
            "adapter_id": self.adapter_id,
            "effect_class": self.effect_class,
            "target_state_ref_hash": self.target_state_ref_hash,
            "idempotency_key_hash": self.idempotency_key_hash,
            "source_binding_hash": self.source_binding_hash,
            "execution_authorized": self.execution_authorized,
            "privileged_broker_request": self.privileged_broker_request,
            "allow_legacy_mechanical_shell": self.allow_legacy_mechanical_shell,
            "side_effects_executed": self.side_effects_executed,
        }


_ROUTE_BINDINGS: Mapping[tuple[str, str], RouteAuthorityBinding] = MappingProxyType({
    (ROUTE_CODE_GENERATION, "codegen"): RouteAuthorityBinding(
        route=ROUTE_CODE_GENERATION,
        operation="codegen",
        lane=Lane.CODEGEN,
        adapter_id="adapter:repo-codegen",
        operation_kind="workspace_write",
        effects=("workspace_write",),
        reversibility=Reversibility.REVERSIBLE,
        requires_runner_task=True,
    ),
    (ROUTE_VALIDATION, "validation"): RouteAuthorityBinding(
        route=ROUTE_VALIDATION,
        operation="validation",
        lane=Lane.VALIDATE,
        adapter_id="adapter:repo-validation",
        operation_kind="validate",
        effects=("validate", "test"),
        reversibility=Reversibility.REVERSIBLE,
        requires_runner_task=True,
    ),
    (ROUTE_PUBLISH_ONLY, "publication"): RouteAuthorityBinding(
        route=ROUTE_PUBLISH_ONLY,
        operation="publication",
        lane=Lane.PUBLISH,
        adapter_id="adapter:draft-publication",
        operation_kind="draft_pr_open",
        effects=("draft_pr_open",),
        reversibility=Reversibility.BOUNDED_REVERSIBLE,
        requires_runner_task=True,
    ),
    (ROUTE_RUNTIME_ONLY, "control"): RouteAuthorityBinding(
        route=ROUTE_RUNTIME_ONLY,
        operation="control",
        lane=Lane.CONTROL,
        adapter_id="adapter:runtime-control",
        operation_kind="bounded_service_restart",
        effects=("bounded_service_restart",),
        reversibility=Reversibility.BOUNDED_REVERSIBLE,
        requires_runner_task=False,
        requires_operator=True,
    ),
    (ROUTE_RECOVERY, "recovery"): RouteAuthorityBinding(
        route=ROUTE_RECOVERY,
        operation="recovery",
        lane=Lane.CONTROL,
        adapter_id="adapter:operation-recovery",
        operation_kind="bounded_service_restart",
        effects=("bounded_service_restart",),
        reversibility=Reversibility.BOUNDED_REVERSIBLE,
        requires_runner_task=False,
        requires_operator=True,
    ),
    (ROUTE_MERGE, "merge"): RouteAuthorityBinding(
        route=ROUTE_MERGE,
        operation="merge",
        lane=Lane.MERGE,
        adapter_id="adapter:exact-operator-merge",
        operation_kind="workspace_bind",
        effects=("protected_merge",),
        reversibility=Reversibility.IRREVERSIBLE,
        requires_runner_task=False,
        requires_operator=True,
        requires_fresh_operator_evidence=True,
    ),
})

_PRODUCTION_DISPATCH_BINDINGS: Mapping[tuple[str, str], tuple[str, str]] = MappingProxyType({
    (ROUTE_CODE_GENERATION, DISPATCH_TASK_CODEGEN): (ROUTE_CODE_GENERATION, "codegen"),
    (ROUTE_VALIDATION, DISPATCH_TASK_VALIDATION): (ROUTE_VALIDATION, "validation"),
    (ROUTE_PUBLISH_ONLY, DISPATCH_TASK_PUBLICATION): (ROUTE_PUBLISH_ONLY, "publication"),
    (ROUTE_RUNTIME_ONLY, DISPATCH_TASK_CONTROL): (ROUTE_RUNTIME_ONLY, "control"),
    (ROUTE_RECOVERY, DISPATCH_TASK_RECOVERY): (ROUTE_RECOVERY, "recovery"),
    (ROUTE_MERGE, DISPATCH_TASK_MERGE): (ROUTE_MERGE, "merge"),
})


def authoritative_route_inventory() -> tuple[RouteAuthorityBinding, ...]:
    return tuple(_ROUTE_BINDINGS[key] for key in sorted(_ROUTE_BINDINGS))


def authoritative_route_inventory_proof() -> dict[str, object]:
    routes = authoritative_route_inventory()
    return {
        "schema": "skeleton.runner_vnext_route_inventory.v1",
        "authoritative_mode": AUTHORITATIVE_MODE,
        "legacy_decision_fallback": False,
        "route_count": len(routes),
        "routes": [
            {
                "route": binding.route,
                "operation": binding.operation,
                "lane": binding.lane.value,
                "adapter_id": binding.adapter_id,
                "requires_operator": binding.requires_operator,
                "requires_fresh_operator_evidence": binding.requires_fresh_operator_evidence,
            }
            for binding in routes
        ],
    }


def production_dispatch_route_binding(
    *,
    production_route: str,
    dispatch_task_class: str,
) -> RouteAuthorityBinding:
    mapped = _PRODUCTION_DISPATCH_BINDINGS.get((production_route, dispatch_task_class))
    if mapped is None:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_DISPATCH_ROUTE_UNMAPPED_FAIL_CLOSED")
    return route_binding(*mapped)


def authoritative_dispatch_inventory_proof(
    active_dispatch: Mapping[str, str],
) -> dict[str, object]:
    if not active_dispatch:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_DISPATCH_INVENTORY_EMPTY")
    entries: list[dict[str, object]] = []
    for production_route, dispatch_task_class in sorted(active_dispatch.items()):
        binding = production_dispatch_route_binding(
            production_route=production_route,
            dispatch_task_class=dispatch_task_class,
        )
        entries.append({
            "production_route": production_route,
            "dispatch_task_class": dispatch_task_class,
            "route": binding.route,
            "operation": binding.operation,
            "lane": binding.lane.value,
            "adapter_id": binding.adapter_id,
            "requires_operator": binding.requires_operator,
            "requires_fresh_operator_evidence": binding.requires_fresh_operator_evidence,
        })
    return {
        "schema": "skeleton.runner_vnext_authoritative_dispatch_inventory.v1",
        "authoritative_mode": AUTHORITATIVE_MODE,
        "legacy_decision_fallback": False,
        "dispatch_count": len(entries),
        "dispatch": entries,
    }


def route_binding(route: str, operation: str) -> RouteAuthorityBinding:
    binding = _ROUTE_BINDINGS.get((route, operation))
    if binding is None:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_ROUTE_UNMAPPED_FAIL_CLOSED")
    return binding


def source_authority_identity(
    runner_task: RunnerTask,
    *,
    source_task_ref: str,
    adapter_id: str,
    route: str,
    operation: str,
) -> SourceAuthorityIdentity:
    _require_public_ref(source_task_ref, "VNEXT_AUTHORITY_SOURCE_TASK_REF_INVALID")
    _require_public_ref(adapter_id, "VNEXT_AUTHORITY_ADAPTER_ID_INVALID")
    runner_task_hash = hashlib.sha256(runner_task.to_json().encode("utf-8")).hexdigest()
    payload = {
        "runner_task_sha256": runner_task_hash,
        "source_task_ref": source_task_ref,
        "adapter_id": adapter_id,
        "route": route,
        "operation": operation,
    }
    binding_hash = _hash_json(payload)
    return SourceAuthorityIdentity(
        runner_task_hash=runner_task_hash,
        source_task_ref=source_task_ref,
        adapter_id=adapter_id,
        binding_hash=binding_hash,
        task_id=f"task:{operation}:{binding_hash}",
        operation_id=f"op:{operation}:{binding_hash}",
        idempotency_key=f"idem:{operation}:{binding_hash}",
    )


def build_authoritative_stores(
    config: RunnerVNextRuntimeConfig,
    *,
    clock: Callable[[], float],
) -> RunnerVNextStores:
    state_root = _stable_sqlite_root(config.state_root)
    ledger_path = _stable_sqlite_path(config.ledger_db_path, state_root=state_root)
    lease_path = _stable_sqlite_path(config.lease_db_path, state_root=state_root)
    if ledger_path == lease_path:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SQLITE_PATH_COLLISION")
    ledger = OperationLedger(ledger_path)
    lease_store = LaneLeaseStore(lease_path, clock=clock)
    return RunnerVNextStores(
        ledger=ledger,
        lease_store=lease_store,
        scheduler=VNextScheduler(lease_store),
    )


def bind_runner_operation(
    *,
    runner_task: RunnerTask,
    route: str,
    operation: str,
    source_task_ref: str | None = None,
    issue_number: int | None = None,
) -> BoundRunnerOperation:
    binding = route_binding(route, operation)
    if not binding.requires_runner_task:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_TYPED_PRIVILEGED_BINDER_REQUIRED")
    source_ref = _source_ref(source_task_ref=source_task_ref, issue_number=issue_number)
    if route == ROUTE_CODE_GENERATION and runner_task.privacy_boundary != ORDINARY_REPOSITORY_PRIVACY:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_CODEGEN_PUBLIC_REPOSITORY_PRIVACY_REQUIRED")
    if not runner_task.allowed_files:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_RESOURCES_REQUIRED")

    identity = source_authority_identity(
        runner_task,
        source_task_ref=source_ref,
        adapter_id=binding.adapter_id,
        route=route,
        operation=operation,
    )
    resources = tuple(f"repo:{path}" for path in runner_task.allowed_files)
    privacy = _runner_task_privacy(runner_task)
    target_state_ref = f"git:{runner_task.base_sha}"
    universal_task = UniversalTask(
        task_id=identity.task_id,
        intent=f"runner:{route}:{operation}",
        domain="github",
        target_resources=resources,
        required_capabilities=runner_task.requested_capabilities,
        privacy=privacy,
        reversibility=binding.reversibility,
        expected_effects=binding.effects,
        validation=tuple(" ".join(command) for command in runner_task.validation_commands),
        rollback=("operator_recovery_required",),
        idempotency_key=identity.idempotency_key,
        operator_boundary_evidence=(
            f"runner_task_sha256:{identity.runner_task_hash}",
            f"source_task_ref:{source_ref}",
            f"adapter_id:{binding.adapter_id}",
            f"approval:{runner_task.approval_reference}",
        ),
    )
    operation_ir = OperationIR(
        identity.operation_id,
        binding.operation_kind,
        resources,
        binding.effects,
        identity.idempotency_key,
    )
    policy_input = PolicyInput(
        operation=operation_ir,
        reversibility=binding.reversibility,
        privacy=privacy,
        target_state_ref=target_state_ref,
        operator_boundary_evidence=universal_task.operator_boundary_evidence,
    )
    return BoundRunnerOperation(
        route=route,
        operation=operation,
        binding=binding,
        runner_task=runner_task,
        source_task_ref=source_ref,
        runner_task_hash=identity.runner_task_hash,
        source_binding_hash=identity.binding_hash,
        universal_task=universal_task,
        operation_ir=operation_ir,
        policy_input=policy_input,
        policy_decision=classify_policy(policy_input),
        target_state_ref=target_state_ref,
        lease_scope_key=f"repo:{runner_task.repo}@{runner_task.branch}",
    )


def bind_privileged_operation(
    *,
    route: str,
    operation: str,
    authority_input: PrivilegedAuthorityInput,
) -> BoundRunnerOperation:
    binding = route_binding(route, operation)
    if binding.requires_runner_task:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_RUNNER_TASK_BINDER_REQUIRED")
    _require_public_ref(authority_input.source_task_ref, "VNEXT_AUTHORITY_SOURCE_TASK_REF_INVALID")
    _require_public_ref(authority_input.target_state_ref, "VNEXT_AUTHORITY_TARGET_STATE_REF_INVALID")
    _require_public_ref(authority_input.idempotency_seed, "VNEXT_AUTHORITY_IDEMPOTENCY_SEED_INVALID")
    if not authority_input.resources:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_RESOURCES_REQUIRED")
    if not authority_input.required_capabilities:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_CAPABILITIES_REQUIRED")
    for resource in authority_input.resources:
        _require_public_ref(resource, "VNEXT_AUTHORITY_RESOURCE_REF_INVALID")
    if binding.requires_operator and not authority_input.operator_boundary_evidence:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_OPERATOR_EVIDENCE_REQUIRED")
    for evidence in authority_input.operator_boundary_evidence:
        _require_public_ref(evidence, "VNEXT_AUTHORITY_OPERATOR_EVIDENCE_INVALID")

    payload = {
        "source_task_ref": authority_input.source_task_ref,
        "adapter_id": binding.adapter_id,
        "route": route,
        "operation": operation,
        "target_state_ref": authority_input.target_state_ref,
        "resources": list(authority_input.resources),
        "required_capabilities": list(authority_input.required_capabilities),
        "privacy": authority_input.privacy.value,
        "operator_boundary_evidence": list(authority_input.operator_boundary_evidence),
        "idempotency_seed": authority_input.idempotency_seed,
    }
    binding_hash = _hash_json(payload)
    task_id = f"task:{operation}:{binding_hash}"
    operation_id = f"op:{operation}:{binding_hash}"
    idempotency_key = f"idem:{operation}:{binding_hash}"
    universal_task = UniversalTask(
        task_id=task_id,
        intent=f"runner:{route}:{operation}",
        domain="github",
        target_resources=authority_input.resources,
        required_capabilities=authority_input.required_capabilities,
        privacy=authority_input.privacy,
        reversibility=binding.reversibility,
        expected_effects=binding.effects,
        validation=(),
        rollback=("operator_recovery_required",),
        idempotency_key=idempotency_key,
        operator_boundary_evidence=authority_input.operator_boundary_evidence,
    )
    operation_ir = OperationIR(
        operation_id,
        binding.operation_kind,
        authority_input.resources,
        binding.effects,
        idempotency_key,
    )
    policy_input = PolicyInput(
        operation=operation_ir,
        reversibility=binding.reversibility,
        privacy=authority_input.privacy,
        target_state_ref=authority_input.target_state_ref,
        operator_boundary_evidence=authority_input.operator_boundary_evidence,
    )
    decision = classify_policy(policy_input)
    if binding.requires_fresh_operator_evidence and decision.effect_class is not EffectClass.RED:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_RED_POLICY_REQUIRED")
    return BoundRunnerOperation(
        route=route,
        operation=operation,
        binding=binding,
        runner_task=None,
        source_task_ref=authority_input.source_task_ref,
        runner_task_hash=None,
        source_binding_hash=binding_hash,
        universal_task=universal_task,
        operation_ir=operation_ir,
        policy_input=policy_input,
        policy_decision=decision,
        target_state_ref=authority_input.target_state_ref,
        lease_scope_key=f"{binding.lane.value}:{binding_hash}",
    )


def default_authority_manifests() -> Mapping[str, AdapterManifest]:
    return MappingProxyType({
        "adapter:repo-codegen": AdapterManifest(
            adapter_id="adapter:repo-codegen",
            operation_kinds=("workspace_write",),
            capabilities=("repository_read", "repository_write_allowlisted", "test_execution"),
            allowed_effect_classes=(EffectClass.GREEN,),
            resource_patterns=("repo:*",),
            privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
            privileged_pep_required=False,
        ),
        "adapter:repo-validation": AdapterManifest(
            adapter_id="adapter:repo-validation",
            operation_kinds=("validate",),
            capabilities=("repository_read", "repository_write_allowlisted", "test_execution"),
            allowed_effect_classes=(EffectClass.GREEN,),
            resource_patterns=("repo:*",),
            privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
            privileged_pep_required=False,
        ),
        "adapter:draft-publication": AdapterManifest(
            adapter_id="adapter:draft-publication",
            operation_kinds=("draft_pr_open",),
            capabilities=("repository_read", "repository_write_allowlisted", "test_execution", "publish_pull_request"),
            allowed_effect_classes=(EffectClass.GREEN,),
            resource_patterns=("repo:*",),
            privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
            privileged_pep_required=False,
        ),
        "adapter:runtime-control": AdapterManifest(
            adapter_id="adapter:runtime-control",
            operation_kinds=("bounded_service_restart",),
            capabilities=("repository_maintenance", "diagnostic_read", "subprocess_isolated"),
            allowed_effect_classes=(EffectClass.GREEN, EffectClass.YELLOW),
            resource_patterns=("service-config:*", "control:*", "repo:*"),
            privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
            privileged_pep_required=True,
        ),
        "adapter:operation-recovery": AdapterManifest(
            adapter_id="adapter:operation-recovery",
            operation_kinds=("bounded_service_restart",),
            capabilities=("repository_maintenance", "diagnostic_read", "subprocess_isolated"),
            allowed_effect_classes=(EffectClass.GREEN, EffectClass.YELLOW),
            resource_patterns=("control:*", "service-config:*", "repo:*"),
            privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
            privileged_pep_required=True,
        ),
        "adapter:exact-operator-merge": AdapterManifest(
            adapter_id="adapter:exact-operator-merge",
            operation_kinds=("workspace_bind",),
            capabilities=("publish_pull_request",),
            allowed_effect_classes=(EffectClass.RED,),
            resource_patterns=("protected:*", "repo:*"),
            privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
            privileged_pep_required=True,
        ),
    })


def default_environment_registry() -> LaneEnvironmentPolicyRegistry:
    policies = {
        f"lane:{lane.value}": LaneEnvironmentPolicy(
            f"lane:{lane.value}",
            ("HOME", "PATH"),
            (),
            ("OPENROUTER_API_KEY", "BWS_ACCESS_TOKEN", "SKELETON_TG_BOT"),
            (),
        )
        for lane in (Lane.CODEGEN, Lane.VALIDATE, Lane.PUBLISH, Lane.CONTROL, Lane.MERGE, Lane.PRIVILEGED)
    }
    return LaneEnvironmentPolicyRegistry(policies)


def prepare_green_authority(
    *,
    bound: BoundRunnerOperation,
    nodes: NodeCapabilityRegistry,
    stores: RunnerVNextStores,
    ttl_seconds: float,
    now: float,
    parent_environment: Mapping[str, str],
) -> GreenExecutionHandoff:
    if bound.policy_decision.effect_class is not EffectClass.GREEN:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_PRIVILEGED_OPERATION_REQUIRES_BROKER")
    control = GreenControlPlane(
        route_planner=RoutePlanner(nodes),
        environment_registry=default_environment_registry(),
        scheduler=stores.scheduler,
        adapter_planner=AdapterPlanner(default_authority_manifests()),
        ledger=stores.ledger,
    )
    return control.prepare(
        task=bound.universal_task,
        operation=bound.operation_ir,
        adapter_id=bound.binding.adapter_id,
        lane=bound.binding.lane,
        lease_scope_key=bound.lease_scope_key,
        target_state_ref=bound.target_state_ref,
        ttl_seconds=ttl_seconds,
        now=now,
        parent_environment=parent_environment,
        injected_environment={},
    )


def grant_green_authority(
    *,
    handoff: GreenExecutionHandoff,
    nodes: NodeCapabilityRegistry,
    stores: RunnerVNextStores,
    target_state_verifier: TargetStateVerifier,
    now: float,
) -> GreenExecutionGrant:
    gate = GreenExecutionGate(
        nodes=nodes,
        environment_registry=default_environment_registry(),
        scheduler=stores.scheduler,
        ledger=stores.ledger,
        target_state_verifier=target_state_verifier,
    )
    return gate.grant(handoff, now=now)


def reserve_privileged_authority(
    *,
    bound: BoundRunnerOperation,
    ledger: OperationLedger,
    fence_token: int,
) -> ExecutionEnvelope:
    if bound.policy_decision.effect_class is EffectClass.GREEN:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_PRIVILEGED_EFFECT_REQUIRED")
    manifest = default_authority_manifests()[bound.binding.adapter_id]
    envelope = AdapterPlanner({manifest.adapter_id: manifest}).plan(
        adapter_id=manifest.adapter_id,
        task=bound.universal_task,
        operation=bound.operation_ir,
        policy_input=bound.policy_input,
        decision=bound.policy_decision,
        target_state_ref=bound.target_state_ref,
        fence_token=fence_token,
    )
    identity = OperationIdentity(
        bound.operation_ir.operation_id,
        bound.operation_ir.idempotency_key,
        bound.target_state_ref,
    )
    ledger.reserve(
        identity,
        fence_token=fence_token,
        reservation_scope_hash=reservation_scope_fingerprint(envelope),
    )
    return envelope


def authorize_privileged_broker_request(
    *,
    envelope: ExecutionEnvelope,
    ledger: OperationLedger,
    authority_claim: AuthorityClaim,
    authority_verifier: AuthorityVerifier,
) -> PrivilegedBrokerRequest:
    try:
        request = PolicyEnforcementPoint().evaluate(
            envelope,
            ledger,
            authority_claim=authority_claim,
            authority_verifier=authority_verifier,
        )
    except PEPError as exc:
        raise RunnerVNextAuthorityError(exc.reason_code) from exc
    if not isinstance(request, PrivilegedBrokerRequest):
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_PRIVILEGED_BROKER_REQUIRED")
    return request


def authority_receipt_from_bound(
    bound: BoundRunnerOperation,
    *,
    status: str,
    reason_code: str,
) -> RunnerVNextAuthorityReceipt:
    effect_class = bound.policy_decision.effect_class
    return RunnerVNextAuthorityReceipt(
        mode=AUTHORITATIVE_MODE,
        status=status,
        reason_code=reason_code,
        route=bound.route,
        operation=bound.operation,
        lane=bound.binding.lane.value,
        adapter_id=bound.binding.adapter_id,
        effect_class=effect_class.value,
        target_state_ref_hash=_text_hash(bound.target_state_ref),
        idempotency_key_hash=_text_hash(bound.operation_ir.idempotency_key),
        source_binding_hash=bound.source_binding_hash,
        execution_authorized=False,
        privileged_broker_request=effect_class is not EffectClass.GREEN,
        allow_legacy_mechanical_shell=False,
        side_effects_executed=False,
    )


def _source_ref(*, source_task_ref: str | None, issue_number: int | None) -> str:
    if source_task_ref is not None and issue_number is not None:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SOURCE_TASK_REF_AMBIGUOUS")
    if source_task_ref is None:
        if issue_number is None or issue_number < 1:
            raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SOURCE_TASK_REF_REQUIRED")
        source_task_ref = f"issue:{issue_number}"
    _require_public_ref(source_task_ref, "VNEXT_AUTHORITY_SOURCE_TASK_REF_INVALID")
    return source_task_ref


def _runner_task_privacy(runner_task: RunnerTask) -> PrivacyClass:
    if runner_task.privacy_boundary == ORDINARY_REPOSITORY_PRIVACY:
        return PrivacyClass.PUBLIC_SAFE
    return PrivacyClass.PRIVATE


def _stable_sqlite_root(value: str) -> Path:
    if not value or value == ":memory:":
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SQLITE_ROOT_REQUIRED")
    raw = Path(value)
    if not raw.is_absolute() or "~" in raw.parts or ".." in raw.parts:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SQLITE_ROOT_UNSAFE")
    root = raw.resolve()
    if root == Path("/"):
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SQLITE_ROOT_UNSAFE")
    return root


def _stable_sqlite_path(value: str, *, state_root: Path) -> Path:
    if not value or value == ":memory:":
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SQLITE_PATH_REQUIRED")
    raw = Path(value)
    if not raw.is_absolute() or "~" in raw.parts or ".." in raw.parts:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SQLITE_PATH_UNSAFE")
    path = raw.resolve()
    try:
        path.relative_to(state_root)
    except ValueError as exc:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SQLITE_PATH_OUTSIDE_ROOT") from exc
    if path.suffix not in {".sqlite", ".sqlite3", ".db"}:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SQLITE_PATH_SUFFIX_REQUIRED")
    return path


def _require_public_ref(value: str, reason_code: str) -> None:
    if (
        not value
        or value.startswith(("/", "~"))
        or "\\" in value
        or ".." in value
        or ":" not in value
        or any(ch.isspace() for ch in value)
    ):
        raise RunnerVNextAuthorityError(reason_code)


def _hash_json(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
