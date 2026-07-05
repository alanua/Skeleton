from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from core.action_gate import (
    ActionGateDecision,
    ActionGateRequest,
    validate_action_request,
)
from core.gate_engine import GateDecision, GateEngine, GateResult
from core.runner_task import (
    PRIVACY_BOUNDARIES,
    REQUESTED_CAPABILITIES,
    TASK_KINDS,
    RunnerTask,
    RunnerTaskValidationError,
)


RUNNER_GATE_CONTRACT_VERSION: Final = "skeleton.runner_gate.v1"
REGISTERED_REPOSITORIES: Final = frozenset({"alanua/Skeleton"})

PROTECTED_PATHS: Final = (
    "BOOT_MANIFEST.yaml",
    "PROJECT_TREE.yaml",
    "OPERATOR_RULES.yaml",
    "CAPABILITY_REGISTRY.yaml",
    ".github/workflows",
    "scripts/runner_poll_github_tasks.py",
    "core/gate_engine.py",
    "core/action_gate.py",
    "secrets",
    "deploy",
    "server",
    "finance",
    "legal",
    "governance",
    "Runner_core",
    "adapter_boundaries",
)
PROTECTED_PATH_PREFIXES: Final = ("core/runner_",)

ROUTE_REQUIRED_CAPABILITIES: Final = {
    "code_edit": frozenset(
        {
            "repository_read",
            "repository_write_allowlisted",
            "test_execution",
        }
    ),
    "repository_maintenance": frozenset(
        {"repository_read", "repository_maintenance"}
    ),
    "private_memory": frozenset({"memory_gateway_read"}),
    "diagnostic": frozenset({"diagnostic_read"}),
    "loop_control": frozenset({"loop_control"}),
    "publish": frozenset({"repository_read", "publish_pull_request"}),
}

ROUTE_PRIVACY_BOUNDARIES: Final = {
    "code_edit": frozenset({"PUBLIC_SAFE_REPOSITORY_ONLY"}),
    "repository_maintenance": frozenset({"PUBLIC_SAFE_REPOSITORY_ONLY"}),
    "private_memory": frozenset(
        {"LOCAL_PRIVATE", "PRIVATE_LOCAL", "PUBLIC_SAFE_AGGREGATE_ONLY"}
    ),
    "diagnostic": frozenset(
        {"PUBLIC_SAFE_REPOSITORY_ONLY", "PUBLIC_SAFE_AGGREGATE_ONLY"}
    ),
    "loop_control": frozenset(
        {"PUBLIC_SAFE_REPOSITORY_ONLY", "PUBLIC_SAFE_AGGREGATE_ONLY"}
    ),
    "publish": frozenset({"PUBLIC_SAFE_REPOSITORY_ONLY"}),
}

DURABLE_WRITE_CAPABILITIES: Final = frozenset(
    {
        "repository_write_allowlisted",
        "repository_maintenance",
        "memory_gateway_write",
        "loop_control",
        "publish_pull_request",
    }
)


@dataclass(frozen=True)
class RunnerGateContext:
    repo: str
    branch: str
    base_sha: str
    target_files: tuple[str, ...]
    available_capabilities: tuple[str, ...]
    registered_task_kinds: tuple[str, ...]
    approved_references: tuple[str, ...]
    protected_approval_references: tuple[str, ...] = ()
    patch_plan: Mapping[str, Any] | None = None
    action_request: ActionGateRequest | None = None
    current_head_sha: str | None = None


@dataclass(frozen=True)
class RunnerGateDecision:
    status: str
    reason_codes: tuple[str, ...]
    reasons: tuple[str, ...]
    patch_gate_decision: GateDecision | None
    action_gate_decision: ActionGateDecision | None

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"


class RunnerGate:
    """Fail-closed shadow gate composing Runner, patch, and action contracts."""

    def __init__(
        self,
        *,
        registered_repositories: Iterable[str] = REGISTERED_REPOSITORIES,
        protected_paths: Iterable[str] = PROTECTED_PATHS,
        route_required_capabilities: Mapping[str, Iterable[str]] = (
            ROUTE_REQUIRED_CAPABILITIES
        ),
        route_privacy_boundaries: Mapping[str, Iterable[str]] = (
            ROUTE_PRIVACY_BOUNDARIES
        ),
        gate_engine: GateEngine | None = None,
        action_validator: Callable[[ActionGateRequest], ActionGateDecision] = (
            validate_action_request
        ),
    ) -> None:
        self._registered_repositories = _normalized_values(
            registered_repositories,
            field="registered_repositories",
        )
        self._protected_paths = _normalized_values(
            protected_paths,
            field="protected_paths",
        )
        self._route_required_capabilities = _normalized_route_map(
            route_required_capabilities,
            allowed_values=REQUESTED_CAPABILITIES,
            field="route_required_capabilities",
        )
        self._route_privacy_boundaries = _normalized_route_map(
            route_privacy_boundaries,
            allowed_values=PRIVACY_BOUNDARIES,
            field="route_privacy_boundaries",
        )
        self._gate_engine = gate_engine or GateEngine()
        if not callable(action_validator):
            raise TypeError("action_validator must be callable")
        self._action_validator = action_validator

    def evaluate(self, task: object, context: object) -> RunnerGateDecision:
        reasons: list[tuple[str, str]] = []
        patch_decision: GateDecision | None = None
        action_decision: ActionGateDecision | None = None

        normalized_task = _validated_task(task, reasons)
        normalized_context = _validated_context(context, reasons)
        if normalized_task is None or normalized_context is None:
            return _decision(reasons, patch_decision, action_decision)

        task = normalized_task
        context = normalized_context

        if task.repo not in self._registered_repositories:
            _add(
                reasons,
                "REPOSITORY_NOT_REGISTERED",
                "task repository is not registered for Runner execution",
            )
        if context.repo != task.repo:
            _add(reasons, "REPOSITORY_MISMATCH", "context repository does not match task")
        if context.branch != task.branch:
            _add(reasons, "BRANCH_MISMATCH", "context branch does not match task")
        if context.base_sha.lower() != task.base_sha:
            _add(reasons, "BASE_SHA_MISMATCH", "context base SHA does not match pinned task SHA")

        registered_task_kinds = _validated_string_tuple(
            context.registered_task_kinds,
            field="registered_task_kinds",
            allowed=TASK_KINDS,
            reasons=reasons,
        )
        if registered_task_kinds is not None and task.task_kind not in registered_task_kinds:
            _add(
                reasons,
                "EXECUTOR_NOT_REGISTERED",
                "task kind has no registered executor in the current runtime",
            )

        available_capabilities = _validated_string_tuple(
            context.available_capabilities,
            field="available_capabilities",
            allowed=REQUESTED_CAPABILITIES,
            reasons=reasons,
        )
        if available_capabilities is not None:
            missing_runtime = sorted(
                set(task.requested_capabilities) - set(available_capabilities)
            )
            if missing_runtime:
                _add(
                    reasons,
                    "CAPABILITY_NOT_AVAILABLE",
                    "runtime is missing requested capabilities: " + ", ".join(missing_runtime),
                )

        route_required = self._route_required_capabilities[task.task_kind]
        missing_route = sorted(route_required - set(task.requested_capabilities))
        if missing_route:
            _add(
                reasons,
                "MISSING_ROUTE_CAPABILITY",
                "task is missing route capabilities: " + ", ".join(missing_route),
            )

        allowed_privacy = self._route_privacy_boundaries[task.task_kind]
        if task.privacy_boundary not in allowed_privacy:
            _add(
                reasons,
                "PRIVACY_BOUNDARY_MISMATCH",
                "privacy boundary is incompatible with the task kind",
            )

        approved_references = _validated_string_tuple(
            context.approved_references,
            field="approved_references",
            allowed=None,
            reasons=reasons,
        )
        if (
            approved_references is not None
            and task.approval_reference not in approved_references
        ):
            _add(
                reasons,
                "APPROVAL_REFERENCE_NOT_APPROVED",
                "task approval reference is not present in the trusted approval set",
            )

        target_files = _validated_paths(context.target_files, reasons)
        if target_files is not None:
            if tuple(sorted(target_files)) != tuple(sorted(task.allowed_files)):
                _add(
                    reasons,
                    "TARGET_FILES_MISMATCH",
                    "runtime target files must exactly match task allowed_files",
                )

            protected_targets = tuple(
                path for path in target_files if self.is_protected_path(path)
            )
            if protected_targets:
                protected_approvals = _validated_string_tuple(
                    context.protected_approval_references,
                    field="protected_approval_references",
                    allowed=None,
                    reasons=reasons,
                )
                if (
                    protected_approvals is not None
                    and task.approval_reference not in protected_approvals
                ):
                    _add(
                        reasons,
                        "PROTECTED_RESOURCE_APPROVAL_MISSING",
                        "protected targets require an explicit protected-resource approval",
                    )

        if DURABLE_WRITE_CAPABILITIES.intersection(task.requested_capabilities):
            plan = dict(context.patch_plan) if isinstance(context.patch_plan, Mapping) else None
            patch_decision = self._gate_engine.check_patch_plan(plan)
            if patch_decision.result is not GateResult.ALLOWED:
                _add(
                    reasons,
                    "PATCH_GATE_BLOCKED",
                    "GateEngine blocked the durable write: "
                    + "; ".join(patch_decision.reasons),
                )
            elif target_files is not None:
                plan_targets = plan.get("target_files") if plan is not None else None
                if (
                    not isinstance(plan_targets, list)
                    or tuple(sorted(plan_targets)) != tuple(sorted(target_files))
                ):
                    _add(
                        reasons,
                        "PATCH_PLAN_TARGET_MISMATCH",
                        "PatchPlan target_files must exactly match runtime target files",
                    )

        publish_requested = (
            task.task_kind == "publish"
            or "publish_pull_request" in task.requested_capabilities
        )
        if publish_requested:
            if not isinstance(context.action_request, ActionGateRequest):
                _add(
                    reasons,
                    "ACTION_GATE_REQUIRED",
                    "publish tasks require an ActionGateRequest",
                )
            else:
                action_decision = self._action_validator(context.action_request)
                if action_decision.status != "allowed":
                    _add(
                        reasons,
                        "ACTION_GATE_BLOCKED",
                        "ActionGate blocked the publish action: "
                        + "; ".join(action_decision.reasons),
                    )
                _validate_action_parity(task, context, target_files, reasons)

        return _decision(reasons, patch_decision, action_decision)

    def is_protected_path(self, path: str) -> bool:
        return any(
            path == protected or path.startswith(protected + "/")
            for protected in self._protected_paths
        ) or any(
            path.startswith(prefix) for prefix in PROTECTED_PATH_PREFIXES
        )


def _validated_task(
    task: object,
    reasons: list[tuple[str, str]],
) -> RunnerTask | None:
    if not isinstance(task, RunnerTask):
        _add(reasons, "INVALID_RUNNER_TASK", "gate input must be a RunnerTask")
        return None
    try:
        return RunnerTask.from_mapping(task.to_mapping())
    except (RunnerTaskValidationError, TypeError, ValueError, AttributeError) as exc:
        _add(reasons, "INVALID_RUNNER_TASK", f"RunnerTask failed structural validation: {exc}")
        return None


def _validated_context(
    context: object,
    reasons: list[tuple[str, str]],
) -> RunnerGateContext | None:
    if not isinstance(context, RunnerGateContext):
        _add(reasons, "INVALID_GATE_CONTEXT", "gate context must be a RunnerGateContext")
        return None
    if not all(
        isinstance(value, str) and value
        for value in (context.repo, context.branch)
    ):
        _add(
            reasons,
            "INVALID_GATE_CONTEXT",
            "context repo and branch must be non-empty strings",
        )
        return None
    if not _is_full_sha(context.base_sha):
        _add(
            reasons,
            "INVALID_GATE_CONTEXT",
            "context base_sha must be a full 40-character Git SHA",
        )
        return None
    if (
        context.current_head_sha is not None
        and not _is_full_sha(context.current_head_sha)
    ):
        _add(
            reasons,
            "INVALID_GATE_CONTEXT",
            "current_head_sha must be a full 40-character Git SHA",
        )
        return None
    return context


def _validated_string_tuple(
    values: object,
    *,
    field: str,
    allowed: frozenset[str] | None,
    reasons: list[tuple[str, str]],
) -> tuple[str, ...] | None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not value for value in values
    ):
        _add(reasons, "INVALID_GATE_CONTEXT", f"{field} must be a tuple of strings")
        return None
    if len(set(values)) != len(values):
        _add(reasons, "INVALID_GATE_CONTEXT", f"{field} must not contain duplicates")
        return None
    if allowed is not None:
        unknown = sorted(set(values) - allowed)
        if unknown:
            _add(
                reasons,
                "INVALID_GATE_CONTEXT",
                f"{field} contains unknown values: {', '.join(unknown)}",
            )
            return None
    return values


def _validated_paths(
    values: object,
    reasons: list[tuple[str, str]],
) -> tuple[str, ...] | None:
    if not isinstance(values, tuple) or not values:
        _add(
            reasons,
            "INVALID_TARGET_FILES",
            "target_files must be a non-empty tuple",
        )
        return None
    if len(set(values)) != len(values) or any(not _safe_path(value) for value in values):
        _add(
            reasons,
            "INVALID_TARGET_FILES",
            "target_files must contain unique safe repository-relative paths",
        )
        return None
    return values


def _safe_path(value: object) -> bool:
    if not isinstance(value, str) or not value or value.strip() != value:
        return False
    parts = value.split("/")
    return (
        not value.startswith("/")
        and "\\" not in value
        and all(part not in {"", ".", ".."} for part in parts)
    )


def _is_full_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _validated_action_files(
    values: object,
    reasons: list[tuple[str, str]],
) -> tuple[str, ...] | None:
    if (
        not isinstance(values, tuple)
        or not values
        or any(not isinstance(value, str) for value in values)
        or len(set(values)) != len(values)
        or any(not _safe_path(value) for value in values)
    ):
        _add(
            reasons,
            "ACTION_FILES_INVALID",
            "ActionGateRequest expected_files must contain unique safe paths",
        )
        return None
    return values


def _validate_action_parity(
    task: RunnerTask,
    context: RunnerGateContext,
    target_files: tuple[str, ...] | None,
    reasons: list[tuple[str, str]],
) -> None:
    request = context.action_request
    assert isinstance(request, ActionGateRequest)

    if request.repo != task.repo:
        _add(
            reasons,
            "ACTION_REPOSITORY_MISMATCH",
            "ActionGateRequest repository does not match RunnerTask",
        )
    action_files = _validated_action_files(request.expected_files, reasons)
    if (
        target_files is not None
        and action_files is not None
        and tuple(sorted(action_files)) != tuple(sorted(target_files))
    ):
        _add(
            reasons,
            "ACTION_FILES_MISMATCH",
            "ActionGateRequest expected_files do not match runtime target files",
        )
    if context.current_head_sha is None:
        _add(
            reasons,
            "CURRENT_HEAD_SHA_REQUIRED",
            "publish tasks require the current PR head SHA",
        )
    elif not _is_full_sha(request.expected_head_sha):
        _add(
            reasons,
            "ACTION_HEAD_SHA_INVALID",
            "ActionGateRequest expected_head_sha must be a full Git SHA",
        )
    elif request.expected_head_sha.lower() != context.current_head_sha.lower():
        _add(
            reasons,
            "ACTION_HEAD_SHA_MISMATCH",
            "ActionGateRequest expected_head_sha does not match current PR head SHA",
        )

    payload_pr_number = task.payload.get("pr_number")
    if isinstance(payload_pr_number, int) and not isinstance(payload_pr_number, bool):
        if request.pr_number != payload_pr_number:
            _add(
                reasons,
                "ACTION_PR_MISMATCH",
                "ActionGateRequest PR number does not match task payload",
            )


def _normalized_values(values: Iterable[str], *, field: str) -> frozenset[str]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{field} must be an iterable of strings")
    normalized = tuple(values)
    if not normalized or any(not isinstance(value, str) or not value for value in normalized):
        raise ValueError(f"{field} must contain non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} must not contain duplicates")
    return frozenset(normalized)


def _normalized_route_map(
    values: Mapping[str, Iterable[str]],
    *,
    allowed_values: frozenset[str],
    field: str,
) -> dict[str, frozenset[str]]:
    if set(values) != TASK_KINDS:
        raise ValueError(f"{field} must define exactly all Runner task kinds")
    normalized: dict[str, frozenset[str]] = {}
    for task_kind in sorted(TASK_KINDS):
        route_values = _normalized_values(values[task_kind], field=f"{field}.{task_kind}")
        unknown = route_values - allowed_values
        if unknown:
            raise ValueError(
                f"{field}.{task_kind} contains unknown values: {', '.join(sorted(unknown))}"
            )
        normalized[task_kind] = route_values
    return normalized


def _add(reasons: list[tuple[str, str]], code: str, message: str) -> None:
    if code not in {existing_code for existing_code, _ in reasons}:
        reasons.append((code, message))


def _decision(
    reasons: list[tuple[str, str]],
    patch_decision: GateDecision | None,
    action_decision: ActionGateDecision | None,
) -> RunnerGateDecision:
    return RunnerGateDecision(
        status="blocked" if reasons else "allowed",
        reason_codes=tuple(code for code, _ in reasons),
        reasons=tuple(message for _, message in reasons),
        patch_gate_decision=patch_decision,
        action_gate_decision=action_decision,
    )
