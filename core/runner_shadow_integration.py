from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, Final

from core.action_gate import ActionGateRequest
from core.runner_executor import CallableRunnerExecutor, RunnerExecutorError
from core.runner_executor_registry import RunnerExecutorRegistry
from core.runner_gate import ROUTE_REQUIRED_CAPABILITIES, RunnerGate, RunnerGateContext
from core.runner_task import (
    RUNNER_TASK_SCHEMA,
    RunnerTask,
    RunnerTaskValidationError,
)


SHADOW_RECEIPT_SCHEMA: Final = "skeleton.runner_shadow_receipt.v1"

LEGACY_ROUTE_CODE_GENERATION: Final = "code_generation"
LEGACY_ROUTE_RUNTIME_ONLY: Final = "runtime_only"
LEGACY_ROUTE_PUBLISH_ONLY: Final = "publish_only"

SIGNED_MERGE_MODE: Final = "signed_merge"
NON_MERGE_PUBLISH_REASON: Final = "NON_MERGE_PUBLISH_ROUTE_NOT_APPLICABLE"

ROUTE_TO_REQUIRED_CAPABILITIES: Final = MappingProxyType(
    {
        route: frozenset(capabilities)
        for route, capabilities in ROUTE_REQUIRED_CAPABILITIES.items()
    }
)

MAINTENANCE_TASK_KIND_BY_ID: Final = MappingProxyType(
    {
        "backfill_skeleton_memory_recent": "private_memory",
        "check_project_checkout": "repository_maintenance",
        "check_skeleton_freshness": "repository_maintenance",
        "ensure_project_checkout": "repository_maintenance",
        "ensure_telegram_callback_local_config": "repository_maintenance",
        "hermes_memory_gateway_smoke": "private_memory",
        "hermes_private_memory_bridge_check": "private_memory",
        "private_memory_healthcheck": "private_memory",
        "recover_skeleton_checkout": "repository_maintenance",
        "runtime_sync_main": "repository_maintenance",
        "sync_telegram_callback_poller_runtime": "repository_maintenance",
        "home_edge_01_lan_inventory_read_only": "diagnostic",
        "home_edge_01_read_only_diagnostic": "diagnostic",
        "hermes_worker_preflight": "diagnostic",
        "inspect_pr_mergeability": "diagnostic",
        "mempalace_synthetic_runtime_smoke": "diagnostic",
        "validate_pr_branch": "diagnostic",
        "loop_engine_packet": "loop_control",
    }
)

PUBLISH_MAINTENANCE_TASK_IDS: Final = frozenset(
    {
        "inspect_issue_worktree_for_publish",
        "publish_issue_worktree_pr",
        "publish_existing_issue_worktree",
        "publish_issue_worktree_to_existing_pr",
        "overlay_registered_worktree_to_existing_pr",
        "publish_target_project_issue_worktree_pr",
        "publish_container_validation_worktree",
    }
)

DEFAULT_VALIDATION_COMMANDS: Final = (("python3", "-m", "pytest", "-q"),)
DEFAULT_FORBIDDEN_ACTIONS: Final = (
    "no runtime activation",
    "no lease acquisition",
    "no executor dispatch",
)
DEFAULT_EXPECTED_OUTPUT: Final = ("bounded public-safe shadow parity receipt",)

RECEIPT_KEYS: Final = (
    "schema",
    "shadow_status",
    "semantic_route",
    "reason_codes",
    "task_envelope_hash",
)


@dataclass(frozen=True)
class RunnerShadowReceipt:
    schema: str
    shadow_status: str
    semantic_route: str | None
    reason_codes: tuple[str, ...]
    task_envelope_hash: str | None

    def to_public_mapping(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "shadow_status": self.shadow_status,
            "semantic_route": self.semantic_route,
            "reason_codes": list(self.reason_codes),
            "task_envelope_hash": self.task_envelope_hash,
        }


@dataclass(frozen=True)
class RunnerShadowCompatibilityBindings:
    legacy_routes: frozenset[str]
    route_task_kind_by_route: Mapping[str, str]
    maintenance_task_kind_by_id: Mapping[str, str]
    publish_maintenance_task_ids: frozenset[str]

    @property
    def registered_task_kinds(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    *self.route_task_kind_by_route.values(),
                    *self.maintenance_task_kind_by_id.values(),
                }
            )
        )


DEFAULT_COMPATIBILITY_BINDINGS: Final = RunnerShadowCompatibilityBindings(
    legacy_routes=frozenset(
        {
            LEGACY_ROUTE_CODE_GENERATION,
            LEGACY_ROUTE_RUNTIME_ONLY,
            LEGACY_ROUTE_PUBLISH_ONLY,
        }
    ),
    route_task_kind_by_route=MappingProxyType(
        {
            LEGACY_ROUTE_CODE_GENERATION: "code_edit",
            LEGACY_ROUTE_PUBLISH_ONLY: "publish",
        }
    ),
    maintenance_task_kind_by_id=MAINTENANCE_TASK_KIND_BY_ID,
    publish_maintenance_task_ids=PUBLISH_MAINTENANCE_TASK_IDS,
)


def blocked_shadow_receipt(reason_code: str) -> RunnerShadowReceipt:
    return _receipt("blocked", None, (reason_code,), None)


def evaluate_shadow_from_normalized_metadata(
    metadata: Mapping[str, Any],
    compatibility: RunnerShadowCompatibilityBindings = DEFAULT_COMPATIBILITY_BINDINGS,
) -> RunnerShadowReceipt:
    """Evaluate typed Runner parity from normalized legacy metadata only."""
    try:
        task_kind = semantic_task_kind(metadata, compatibility)
    except ShadowNotApplicable as exc:
        return _receipt("not_applicable", None, (exc.reason_code,), None)
    except ShadowBlocked as exc:
        return _receipt("blocked", None, (exc.reason_code,), None)

    try:
        task = runner_task_from_normalized_metadata(metadata, task_kind, compatibility)
    except ShadowBlocked as exc:
        return _receipt("blocked", task_kind, (exc.reason_code,), None)
    except RunnerTaskValidationError as exc:
        return _receipt("blocked", task_kind, (exc.reason_code,), None)

    task_hash = task_envelope_hash(task)

    try:
        registry = build_shadow_executor_registry(compatibility)
        registry_reasons = registry_parity_reason_codes(registry, task, compatibility)
    except RunnerExecutorError as exc:
        return _receipt("blocked", task_kind, (exc.reason_code,), task_hash)
    if registry_reasons:
        return _receipt("blocked", task_kind, registry_reasons, task_hash)

    gate_decision = RunnerGate().evaluate(
        task,
        gate_context_from_metadata(metadata, task, registry),
    )
    return _receipt(
        "allowed" if gate_decision.allowed else "blocked",
        task_kind,
        gate_decision.reason_codes,
        task_hash,
    )


def semantic_task_kind(
    metadata: Mapping[str, Any],
    compatibility: RunnerShadowCompatibilityBindings = DEFAULT_COMPATIBILITY_BINDINGS,
) -> str:
    legacy_route = metadata.get("legacy_route")
    if legacy_route not in compatibility.legacy_routes:
        raise ShadowBlocked("LEGACY_ROUTE_BINDING_MISSING")
    if legacy_route == LEGACY_ROUTE_CODE_GENERATION:
        try:
            return compatibility.route_task_kind_by_route[legacy_route]
        except KeyError as exc:
            raise ShadowBlocked("LEGACY_ROUTE_BINDING_MISSING") from exc
    if legacy_route == LEGACY_ROUTE_PUBLISH_ONLY:
        if metadata.get("publish_mode") == SIGNED_MERGE_MODE:
            try:
                return compatibility.route_task_kind_by_route[legacy_route]
            except KeyError as exc:
                raise ShadowBlocked("LEGACY_ROUTE_BINDING_MISSING") from exc
        raise ShadowNotApplicable(NON_MERGE_PUBLISH_REASON)
    if legacy_route != LEGACY_ROUTE_RUNTIME_ONLY:
        raise ShadowBlocked("UNKNOWN_LEGACY_ROUTE")

    maintenance_task_id = metadata.get("maintenance_task_id")
    if not isinstance(maintenance_task_id, str) or not maintenance_task_id:
        if metadata.get("publish_mode") == SIGNED_MERGE_MODE:
            try:
                return compatibility.route_task_kind_by_route[LEGACY_ROUTE_PUBLISH_ONLY]
            except KeyError as exc:
                raise ShadowBlocked("LEGACY_ROUTE_BINDING_MISSING") from exc
        raise ShadowNotApplicable("UNSUPPORTED_RUNTIME_ROUTE")
    if maintenance_task_id in compatibility.publish_maintenance_task_ids:
        raise ShadowNotApplicable(NON_MERGE_PUBLISH_REASON)
    try:
        return compatibility.maintenance_task_kind_by_id[maintenance_task_id]
    except KeyError as exc:
        if maintenance_task_id in MAINTENANCE_TASK_KIND_BY_ID:
            raise ShadowBlocked("LEGACY_MAINTENANCE_BINDING_MISSING") from exc
        raise ShadowNotApplicable("UNSUPPORTED_RUNTIME_ROUTE") from exc


def runner_task_from_normalized_metadata(
    metadata: Mapping[str, Any],
    task_kind: str | None = None,
    compatibility: RunnerShadowCompatibilityBindings = DEFAULT_COMPATIBILITY_BINDINGS,
) -> RunnerTask:
    selected_kind = task_kind or semantic_task_kind(metadata, compatibility)
    base_sha = metadata.get("base_sha")
    if not isinstance(base_sha, str) or not base_sha:
        raise ShadowBlocked("MISSING_PINNED_BASE_SHA")

    allowed_files = _string_tuple(metadata.get("allowed_files"))
    if not allowed_files:
        raise ShadowBlocked("MISSING_ALLOWED_FILES")

    approval_reference = metadata.get("approval_reference")
    if not isinstance(approval_reference, str) or not approval_reference:
        raise ShadowBlocked("MISSING_APPROVAL_REFERENCE")

    idempotency_key = metadata.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ShadowBlocked("MISSING_IDEMPOTENCY_KEY")

    timeout = metadata.get("validation_timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise ShadowBlocked("MISSING_VALIDATION_TIMEOUT")

    requested_capabilities = _string_tuple(metadata.get("requested_capabilities"))
    if not requested_capabilities:
        requested_capabilities = tuple(sorted(ROUTE_TO_REQUIRED_CAPABILITIES[selected_kind]))

    return RunnerTask.from_mapping(
        {
            "schema": RUNNER_TASK_SCHEMA,
            "repo": metadata.get("repo"),
            "branch": metadata.get("branch"),
            "base_sha": base_sha,
            "task_kind": selected_kind,
            "payload": _public_payload(metadata),
            "requested_capabilities": list(requested_capabilities),
            "allowed_files": list(allowed_files),
            "forbidden_actions": list(
                _string_tuple(metadata.get("forbidden_actions"))
                or DEFAULT_FORBIDDEN_ACTIONS
            ),
            "validation_commands": _validation_commands(metadata),
            "validation_timeout_seconds": timeout,
            "expected_output": list(
                _string_tuple(metadata.get("expected_output"))
                or DEFAULT_EXPECTED_OUTPUT
            ),
            "privacy_boundary": metadata.get("privacy_boundary"),
            "approval_reference": approval_reference,
            "idempotency_key": idempotency_key,
        }
    )


def task_envelope_hash(task: RunnerTask) -> str:
    return hashlib.sha256(task.to_json().encode("utf-8")).hexdigest()


def build_shadow_executor_registry(
    compatibility: RunnerShadowCompatibilityBindings = DEFAULT_COMPATIBILITY_BINDINGS,
) -> RunnerExecutorRegistry:
    return RunnerExecutorRegistry(
        _legacy_adapter(task_kind)
        for task_kind in compatibility.registered_task_kinds
    )


def registry_parity_reason_codes(
    registry: RunnerExecutorRegistry,
    task: RunnerTask,
    compatibility: RunnerShadowCompatibilityBindings = DEFAULT_COMPATIBILITY_BINDINGS,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if registry.registered_task_kinds != compatibility.registered_task_kinds:
        reasons.append("REGISTRY_ROUTE_MISMATCH")
    try:
        executor = registry.lookup(task.task_kind)
    except RunnerExecutorError:
        reasons.append("EXECUTOR_NOT_REGISTERED")
        return tuple(reasons)
    required_capabilities = tuple(sorted(ROUTE_TO_REQUIRED_CAPABILITIES[task.task_kind]))
    if executor.required_capabilities != required_capabilities:
        reasons.append("REGISTRY_CAPABILITY_MISMATCH")
    if not set(required_capabilities).issubset(task.requested_capabilities):
        reasons.append("REGISTRY_CAPABILITY_MISMATCH")
    return tuple(reasons)


def gate_context_from_metadata(
    metadata: Mapping[str, Any],
    task: RunnerTask,
    registry: RunnerExecutorRegistry,
) -> RunnerGateContext:
    trusted_approvals = _string_tuple(metadata.get("trusted_approval_references"))
    trusted_protected_approvals = _string_tuple(
        metadata.get("trusted_protected_approval_references")
    )
    return RunnerGateContext(
        repo=task.repo,
        branch=task.branch,
        base_sha=task.base_sha,
        target_files=task.allowed_files,
        available_capabilities=task.requested_capabilities,
        registered_task_kinds=registry.registered_task_kinds,
        approved_references=trusted_approvals,
        protected_approval_references=trusted_protected_approvals,
        patch_plan=(
            _patch_plan(task, operator_approved=task.approval_reference in trusted_approvals)
            if _requires_patch_plan(task)
            else None
        ),
        action_request=_action_request(metadata, task),
        current_head_sha=_optional_string(metadata.get("current_head_sha")),
    )


def _legacy_adapter(task_kind: str) -> CallableRunnerExecutor:
    return CallableRunnerExecutor(
        task_kind=task_kind,
        required_capabilities=tuple(sorted(ROUTE_TO_REQUIRED_CAPABILITIES[task_kind])),
        handler=_shadow_handler_must_not_run,
    )


def _patch_plan(task: RunnerTask, *, operator_approved: bool) -> dict[str, object]:
    return {
        "schema": "skeleton.patch_plan.v1",
        "target_files": list(task.allowed_files),
        "change_type": "shadow_parity",
        "reason": "typed Runner shadow parity evaluation",
        "current_rule_read": True,
        "critique": "shadow mode only; legacy dispatch remains authoritative",
        "minimal_patch": "no runtime mutation",
        "verification": ["shadow gate evaluation"],
        "approval_required": True,
        "operator_approval": operator_approved,
    }


def _requires_patch_plan(task: RunnerTask) -> bool:
    return bool(
        {
            "repository_write_allowlisted",
            "repository_maintenance",
            "memory_gateway_write",
            "loop_control",
            "publish_pull_request",
        }.intersection(task.requested_capabilities)
    )


def _action_request(metadata: Mapping[str, Any], task: RunnerTask) -> ActionGateRequest | None:
    if task.task_kind != "publish":
        return None
    if metadata.get("publish_mode") != SIGNED_MERGE_MODE:
        return None
    pr_number = metadata.get("pr_number")
    current_head_sha = metadata.get("current_head_sha")
    signed_merge_approved = task.approval_reference in _string_tuple(
        metadata.get("trusted_approval_references")
    )
    if not isinstance(pr_number, int) or isinstance(pr_number, bool):
        return None
    if not isinstance(current_head_sha, str):
        return None
    return ActionGateRequest(
        action_type="merge_pull_request",
        repo=task.repo,
        pr_number=pr_number,
        expected_head_sha=current_head_sha,
        expected_files=task.allowed_files,
        user_approved=signed_merge_approved,
    )


def _public_payload(metadata: Mapping[str, Any]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key in ("issue_number", "maintenance_task_id", "pr_number"):
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            payload[key] = value
        elif isinstance(value, str) and key == "maintenance_task_id":
            payload[key] = value
    return payload


def _validation_commands(metadata: Mapping[str, Any]) -> list[list[str]]:
    commands = metadata.get("validation_commands")
    if isinstance(commands, (list, tuple)):
        normalized: list[list[str]] = []
        for command in commands:
            command_tuple = _string_tuple(command)
            if not command_tuple:
                return [list(command) for command in DEFAULT_VALIDATION_COMMANDS]
            normalized.append(list(command_tuple))
        if normalized:
            return normalized
    return [list(command) for command in DEFAULT_VALIDATION_COMMANDS]


def _receipt(
    status: str,
    semantic_route: str | None,
    reason_codes: tuple[str, ...],
    task_hash: str | None,
) -> RunnerShadowReceipt:
    return RunnerShadowReceipt(
        schema=SHADOW_RECEIPT_SCHEMA,
        shadow_status=status,
        semantic_route=semantic_route,
        reason_codes=tuple(sorted(set(reason_codes))),
        task_envelope_hash=task_hash,
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, (tuple, list, frozenset, set)):
        return ()
    items = tuple(value)
    if any(not isinstance(item, str) or not item for item in items):
        return ()
    return tuple(sorted(items))


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _shadow_handler_must_not_run(task: RunnerTask) -> None:
    raise AssertionError("shadow registry must not dispatch executor handlers")


class ShadowBlocked(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class ShadowNotApplicable(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
