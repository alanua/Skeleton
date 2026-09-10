from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Mapping

from core.runner_vnext_contracts import (
    EffectClass, OperationIR, PolicyDecision, PolicyInput, PrivacyClass, UniversalTask, classify_policy,
)


class AdapterContractError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class AdapterManifest:
    adapter_id: str
    operation_kinds: tuple[str, ...]
    capabilities: tuple[str, ...]
    allowed_effect_classes: tuple[EffectClass, ...]
    resource_patterns: tuple[str, ...]
    privacy_classes: tuple[PrivacyClass, ...]
    privileged_pep_required: bool


@dataclass(frozen=True)
class ExecutionEnvelope:
    operation_id: str
    adapter_id: str
    target_state_ref: str
    fence_token: int
    idempotency_key: str
    resources: tuple[str, ...]
    effects: tuple[str, ...]
    effect_class: EffectClass
    policy_reason_code: str
    required_capabilities: tuple[str, ...]
    privacy: PrivacyClass
    dry_run: bool = True


class AdapterPlanner:
    """Typed dry-run planner. It validates an adapter boundary but executes nothing."""

    def __init__(self, manifests: Mapping[str, AdapterManifest]) -> None:
        self._manifests = dict(manifests)
        if len(self._manifests) != len(manifests):
            raise AdapterContractError("ADAPTER_ID_DUPLICATE")
        for key, manifest in self._manifests.items():
            _validate_manifest(manifest)
            if key != manifest.adapter_id:
                raise AdapterContractError("ADAPTER_REGISTRY_ID_MISMATCH")

    def plan(
        self,
        *,
        adapter_id: str,
        task: UniversalTask,
        operation: OperationIR,
        policy_input: PolicyInput,
        decision: PolicyDecision,
        target_state_ref: str,
        fence_token: int,
    ) -> ExecutionEnvelope:
        manifest = self._manifests.get(adapter_id)
        if manifest is None:
            raise AdapterContractError("ADAPTER_UNKNOWN")
        if not _public_ref(target_state_ref):
            raise AdapterContractError("ADAPTER_TARGET_STATE_REF_INVALID")
        if fence_token < 1:
            raise AdapterContractError("ADAPTER_FENCE_INVALID")
        if policy_input.operation != operation:
            raise AdapterContractError("ADAPTER_POLICY_OPERATION_MISMATCH")
        if policy_input.target_state_ref != target_state_ref:
            raise AdapterContractError("ADAPTER_POLICY_TARGET_STATE_MISMATCH")
        if task.idempotency_key != operation.idempotency_key:
            raise AdapterContractError("ADAPTER_TASK_OPERATION_IDEMPOTENCY_MISMATCH")
        if task.target_resources != operation.resources or task.expected_effects != operation.effects:
            raise AdapterContractError("ADAPTER_TASK_OPERATION_SCOPE_MISMATCH")
        if task.privacy is not policy_input.privacy:
            raise AdapterContractError("ADAPTER_TASK_POLICY_PRIVACY_MISMATCH")
        if classify_policy(policy_input) != decision:
            raise AdapterContractError("ADAPTER_POLICY_DECISION_MISMATCH")
        required_capabilities = task.required_capabilities
        privacy = task.privacy
        if operation.kind not in manifest.operation_kinds:
            raise AdapterContractError("ADAPTER_OPERATION_KIND_UNSUPPORTED")
        if not set(required_capabilities).issubset(set(manifest.capabilities)):
            raise AdapterContractError("ADAPTER_CAPABILITY_UNSUPPORTED")
        if privacy not in manifest.privacy_classes:
            raise AdapterContractError("ADAPTER_PRIVACY_UNSUPPORTED")
        if decision.effect_class not in manifest.allowed_effect_classes:
            raise AdapterContractError("ADAPTER_EFFECT_CLASS_UNSUPPORTED")
        if decision.effect_class is EffectClass.RED and not manifest.privileged_pep_required:
            raise AdapterContractError("ADAPTER_RED_REQUIRES_PRIVILEGED_PEP")
        if decision.separate_privileged_pep_required and not manifest.privileged_pep_required:
            raise AdapterContractError("ADAPTER_POLICY_REQUIRES_PRIVILEGED_PEP")
        for resource in operation.resources:
            if not _resource_matches(resource, manifest.resource_patterns):
                raise AdapterContractError("ADAPTER_RESOURCE_OUT_OF_SCOPE")
        return ExecutionEnvelope(
            operation_id=operation.operation_id,
            adapter_id=adapter_id,
            target_state_ref=target_state_ref,
            fence_token=fence_token,
            idempotency_key=operation.idempotency_key,
            resources=operation.resources,
            effects=operation.effects,
            effect_class=decision.effect_class,
            policy_reason_code=decision.reason_code,
            required_capabilities=required_capabilities,
            privacy=privacy,
        )


def _validate_manifest(manifest: AdapterManifest) -> None:
    if not manifest.adapter_id or any(ch.isspace() for ch in manifest.adapter_id):
        raise AdapterContractError("ADAPTER_ID_INVALID")
    if not manifest.operation_kinds or not manifest.capabilities or not manifest.resource_patterns:
        raise AdapterContractError("ADAPTER_POSITIVE_DECLARATION_REQUIRED")
    if not manifest.allowed_effect_classes or not manifest.privacy_classes:
        raise AdapterContractError("ADAPTER_BOUNDARY_DECLARATION_REQUIRED")
    for pattern in manifest.resource_patterns:
        _validate_resource_pattern(pattern)


def _public_ref(value: str) -> bool:
    return bool(value) and not value.startswith(("/", "~")) and "\\" not in value and ".." not in value and ":" in value and not any(ch.isspace() for ch in value)


def _validate_resource_pattern(pattern: str) -> None:
    if not _public_ref(pattern) or pattern in {"*", "*:*"}:
        raise AdapterContractError("ADAPTER_RESOURCE_PATTERN_INVALID")
    namespace, _, tail = pattern.partition(":")
    if not namespace or any(ch in namespace for ch in "*?[]") or not tail:
        raise AdapterContractError("ADAPTER_RESOURCE_PATTERN_INVALID")


def _resource_matches(resource: str, patterns: tuple[str, ...]) -> bool:
    if not _public_ref(resource):
        return False
    return any(fnmatch(resource, pattern) for pattern in patterns)
