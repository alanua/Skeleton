from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from typing import Mapping, MutableMapping

from core.execution_fabric import (
    DeliverableContract,
    ExecutionBinding,
    RouteLease,
    TaskProfile,
    build_execution_bindings,
    build_route_lease,
)
from core.executor_registry import load_executor_registry
from core.model_registry import load_model_registry
from integrations.credential_runtime import (
    RegisteredCredentialRuntimeError,
    bind_registered_environment_credential,
)


ROOT = Path(__file__).resolve().parents[1]
EXECUTOR_REGISTRY_PATH = ROOT / "EXECUTOR_REGISTRY.yaml"
MODEL_REGISTRY_PATH = ROOT / "MODEL_REGISTRY.yaml"
OPENHANDS_EXECUTOR_ID = "openhands-external"
OPENROUTER_CREDENTIAL_SERVICE = "runner-openhands"
OPENROUTER_CREDENTIAL_ALIAS = "openrouter-api"
OPENROUTER_CREDENTIAL_ACTION = "bind-openrouter-fallback"
_OPENROUTER_BOUND_KEY_ENV = "SKELETON_OPENROUTER_FALLBACK_API_KEY"

# Provider runtime identifiers are adapter-owned. Task/issue prose never selects them.
_OPENHANDS_RUNTIME_MODEL_BY_MODEL_ID = {
    "openrouter-kimi-k2-challenger": "openrouter/moonshotai/kimi-k2",
}

_QUOTA_OR_PROVIDER_MARKERS = (
    "usage limit",
    "rate limit",
    "quota",
    "insufficient_quota",
    "provider unavailable",
    "temporarily unavailable",
    "service unavailable",
    "try again at",
)


class CodegenRouteError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OpenHandsSecondaryRoute:
    binding: ExecutionBinding
    lease: RouteLease
    runtime_model: str


def codex_failure_allows_secondary(exit_code: int, output: str) -> bool:
    """Allow a secondary executor only for bounded availability failures.

    Ordinary implementation/test/validation failures are not executor-fallback authority.
    """
    if exit_code == 0:
        return False
    lowered = (output or "").lower()
    return any(marker in lowered for marker in _QUOTA_OR_PROVIDER_MARKERS)


def _production_codegen_profile() -> TaskProfile:
    return TaskProfile(
        operation="explicit_secondary_codegen",
        task_class="code_generation",
        required_executor_capabilities=(
            "repository_read",
            "repository_write",
            "test_execution",
        ),
        required_model_capabilities=(
            ("reasoning", 0.70),
            ("repository_edit", 0.70),
            ("tool_use", 0.70),
        ),
        privacy_class="PUBLIC",
        side_effect_class="REPOSITORY_MUTATION",
        deliverable_contract=DeliverableContract(
            require_changed_files=True,
            minimum_changed_files=1,
            require_tests_passed=True,
        ),
        validation_id="runner-codegen-deliverable-v1",
        budget_ref="secondary-codegen-bounded",
        timeout_seconds=1800,
        retry_policy_ref="explicit-secondary-on-provider-unavailable-v1",
        permissions=("repository_read", "repository_write", "test_execution"),
        max_attempts=1,
        max_tokens=0,
        requires_operator=False,
    )


def select_openhands_secondary_route(
    *,
    now: datetime | None = None,
    executor_registry_path: str | Path = EXECUTOR_REGISTRY_PATH,
    model_registry_path: str | Path = MODEL_REGISTRY_PATH,
) -> OpenHandsSecondaryRoute:
    profile = _production_codegen_profile()
    executors = load_executor_registry(executor_registry_path)
    models = load_model_registry(model_registry_path)
    bindings = build_execution_bindings(profile, executors, models, production=True)
    binding = next(
        (candidate for candidate in bindings if candidate.executor_id == OPENHANDS_EXECUTOR_ID),
        None,
    )
    if binding is None or binding.model_id is None:
        raise CodegenRouteError("no_eligible_openhands_secondary_binding")
    runtime_model = _OPENHANDS_RUNTIME_MODEL_BY_MODEL_ID.get(binding.model_id)
    if runtime_model is None:
        raise CodegenRouteError("openhands_runtime_model_unregistered")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise CodegenRouteError("route_time_timezone_required")
    lease = build_route_lease(
        profile,
        binding,
        expires_at=(current.astimezone(UTC) + timedelta(minutes=30)).isoformat(),
    )
    return OpenHandsSecondaryRoute(binding=binding, lease=lease, runtime_model=runtime_model)


def prepare_openhands_secondary_environment(
    *,
    authority_environment: Mapping[str, str] | None = None,
    base_environment: Mapping[str, str] | None = None,
    route: OpenHandsSecondaryRoute | None = None,
) -> tuple[dict[str, str], dict[str, object]]:
    selected = route or select_openhands_secondary_route()
    authority = os.environ if authority_environment is None else authority_environment
    environment: MutableMapping[str, str] = dict(base_environment or {})
    try:
        receipt = bind_registered_environment_credential(
            service_id=OPENROUTER_CREDENTIAL_SERVICE,
            alias=OPENROUTER_CREDENTIAL_ALIAS,
            action_id=OPENROUTER_CREDENTIAL_ACTION,
            environment=environment,
            authority_environment=authority,
        )
    except RegisteredCredentialRuntimeError as exc:
        raise CodegenRouteError("openhands_registered_credential_unavailable") from exc
    api_key = environment.pop(_OPENROUTER_BOUND_KEY_ENV, None)
    if not api_key:
        raise CodegenRouteError("openhands_registered_credential_unavailable")
    environment["LLM_API_KEY"] = api_key
    environment["LLM_MODEL"] = selected.runtime_model
    environment["MAX_BUDGET_PER_TASK"] = "0.50"
    environment["MAX_ITERATIONS"] = "20"
    environment["LLM_NUM_RETRIES"] = "1"
    public_receipt = {
        "executor_id": selected.binding.executor_id,
        "model_id": selected.binding.model_id,
        "binding_id": selected.binding.binding_id,
        "lease_hash": selected.lease.lease_hash,
        "credential_status": "USED",
    }
    return dict(environment), public_receipt


def openhands_secondary_command(task_content: str) -> list[str]:
    return ["openhands", "--headless", "--json", "-t", task_content]
