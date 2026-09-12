from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
from types import MappingProxyType
from typing import Callable, Mapping

from core.runner_task import RunnerTask
from core.runner_vnext_adapters import AdapterManifest, AdapterPlanner, ExecutionEnvelope
from core.runner_vnext_compat import LegacyTaskObservation, adapt_legacy_task
from core.runner_vnext_contracts import (
    EffectClass,
    OperationIR,
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
class BoundRunnerOperation:
    route: str
    operation: str
    binding: RouteAuthorityBinding
    runner_task: RunnerTask | None
    universal_task: UniversalTask
    operation_ir: OperationIR
    policy_input: PolicyInput
    policy_decision: object
    target_state_ref: str
    lease_scope_key: str
    source_binding_hash: str


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


ROUTE_CODE_GENERATION = "code_generation"
ROUTE_VALIDATION = "validation"
ROUTE_PUBLISH_ONLY = "publish_only"
ROUTE_RUNTIME_ONLY = "runtime_only"
ROUTE_RECOVERY = "recovery"
ROUTE_MERGE = "merge"

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


def route_binding(route: str, operation: str) -> RouteAuthorityBinding:
    binding = _ROUTE_BINDINGS.get((route, operation))
    if binding is None:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_ROUTE_UNMAPPED_FAIL_CLOSED")
    return binding


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
    issue_number: int,
) -> BoundRunnerOperation:
    binding = route_binding(route, operation)
    if binding.requires_runner_task and runner_task is None:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_RUNNER_TASK_REQUIRED")
    if binding.route == ROUTE_CODE_GENERATION and runner_task.privacy_boundary != ORDINARY_REPOSITORY_PRIVACY:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_CODEGEN_PUBLIC_REPOSITORY_PRIVACY_REQUIRED")

    target_state_ref = f"git:{runner_task.base_sha}"
    adapted = adapt_legacy_task(
        LegacyTaskObservation(
            source_task_ref=f"issue:{issue_number}",
            target_state_ref=target_state_ref,
            repo=runner_task.repo,
            branch=runner_task.branch,
            task_kind="code_edit" if route in {ROUTE_CODE_GENERATION, ROUTE_VALIDATION} else "publish",
            requested_capabilities=runner_task.requested_capabilities,
            allowed_files=runner_task.allowed_files,
            privacy_boundary=runner_task.privacy_boundary,
            legacy_effect_class=None,
            legacy_status="READY",
            evidence_refs=(f"approval:{runner_task.approval_reference}",),
        )
    )
    resources = adapted.operation.resources
    task_id = f"task:issue-{issue_number}:{operation}"
    idempotency_key = f"idem:{runner_task.idempotency_key}:{operation}"
    operation_id = f"op:issue-{issue_number}:{operation}"
    universal_task = UniversalTask(
        task_id=task_id,
        intent=f"runner:{route}:{operation}",
        domain="github",
        target_resources=resources,
        required_capabilities=runner_task.requested_capabilities,
        privacy=adapted.task.privacy,
        reversibility=binding.reversibility,
        expected_effects=binding.effects,
        validation=tuple(":".join(command) for command in runner_task.validation_commands),
        rollback=("operator_recovery_required",),
        idempotency_key=idempotency_key,
        operator_boundary_evidence=(f"approval:{runner_task.approval_reference}",),
    )
    operation_ir = OperationIR(
        operation_id,
        binding.operation_kind,
        resources,
        binding.effects,
        idempotency_key,
    )
    policy_input = PolicyInput(
        operation=operation_ir,
        reversibility=binding.reversibility,
        privacy=universal_task.privacy,
        target_state_ref=target_state_ref,
        operator_boundary_evidence=universal_task.operator_boundary_evidence,
    )
    decision = classify_policy(policy_input)
    source_hash = _source_binding_hash(runner_task, binding, target_state_ref, resources)
    return BoundRunnerOperation(
        route=route,
        operation=operation,
        binding=binding,
        runner_task=runner_task,
        universal_task=universal_task,
        operation_ir=operation_ir,
        policy_input=policy_input,
        policy_decision=decision,
        target_state_ref=target_state_ref,
        lease_scope_key=f"repo:{runner_task.repo}@{runner_task.branch}",
        source_binding_hash=source_hash,
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
            capabilities=("repository_read", "test_execution", "repository_write_allowlisted"),
            allowed_effect_classes=(EffectClass.GREEN,),
            resource_patterns=("repo:*",),
            privacy_classes=(PrivacyClass.PUBLIC_SAFE,),
            privileged_pep_required=False,
        ),
        "adapter:draft-publication": AdapterManifest(
            adapter_id="adapter:draft-publication",
            operation_kinds=("draft_pr_open",),
            capabilities=("repository_read", "publish_pull_request", "repository_write_allowlisted", "test_execution"),
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
            resource_patterns=("control:*", "repo:*"),
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
        for lane in (Lane.CODEGEN, Lane.VALIDATE, Lane.PUBLISH, Lane.CONTROL, Lane.MERGE)
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
    decision = bound.policy_decision
    if decision.effect_class is EffectClass.GREEN:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_PRIVILEGED_EFFECT_REQUIRED")
    manifest = default_authority_manifests()[bound.binding.adapter_id]
    envelope = AdapterPlanner({manifest.adapter_id: manifest}).plan(
        adapter_id=manifest.adapter_id,
        task=bound.universal_task,
        operation=bound.operation_ir,
        policy_input=bound.policy_input,
        decision=decision,
        target_state_ref=bound.target_state_ref,
        fence_token=fence_token,
    )
    identity = OperationIdentity(bound.operation_ir.operation_id, bound.operation_ir.idempotency_key, bound.target_state_ref)
    ledger.reserve(identity, fence_token=fence_token, reservation_scope_hash=reservation_scope_fingerprint(envelope))
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


def authority_receipt_from_bound(bound: BoundRunnerOperation, *, status: str, reason_code: str) -> RunnerVNextAuthorityReceipt:
    return RunnerVNextAuthorityReceipt(
        mode=AUTHORITATIVE_MODE,
        status=status,
        reason_code=reason_code,
        route=bound.route,
        operation=bound.operation,
        lane=bound.binding.lane.value,
        adapter_id=bound.binding.adapter_id,
        effect_class=bound.policy_decision.effect_class.value,
        target_state_ref_hash=_text_hash(bound.target_state_ref),
        idempotency_key_hash=_text_hash(bound.operation_ir.idempotency_key),
        source_binding_hash=bound.source_binding_hash,
        execution_authorized=bound.policy_decision.effect_class is EffectClass.GREEN,
        privileged_broker_request=bound.policy_decision.effect_class is not EffectClass.GREEN,
        allow_legacy_mechanical_shell=bound.policy_decision.effect_class is EffectClass.GREEN,
    )


def _stable_sqlite_root(value: str) -> Path:
    if not value or value == ":memory:":
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SQLITE_ROOT_REQUIRED")
    raw = Path(value)
    if not raw.is_absolute() or "~" in raw.parts:
        raise RunnerVNextAuthorityError("VNEXT_AUTHORITY_SQLITE_ROOT_UNSAFE")
    root = raw.resolve()
    if root == Path("/") or ".." in raw.parts:
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


def _source_binding_hash(
    runner_task: RunnerTask,
    binding: RouteAuthorityBinding,
    target_state_ref: str,
    resources: tuple[str, ...],
) -> str:
    payload = {
        "runner_task": runner_task.to_mapping(),
        "route": binding.route,
        "operation": binding.operation,
        "lane": binding.lane.value,
        "adapter_id": binding.adapter_id,
        "target_state_ref": target_state_ref,
        "resources": list(resources),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
