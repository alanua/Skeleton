from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from typing import Mapping, MutableMapping

import yaml

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
_OPENHANDS_SECONDARY_MAX_OUTPUT_TOKENS = 768
_OPENHANDS_BOUND_MAX_OUTPUT_TOKENS_ENV = "SKELETON_OPENHANDS_MAX_OUTPUT_TOKENS"
_OPENHANDS_PERSISTENCE_DIR_ENV = "OPENHANDS_PERSISTENCE_DIR"
_OPENHANDS_BOOTSTRAP_REQUIRED_ENV = "SKELETON_OPENHANDS_BOOTSTRAP_REQUIRED"
_OPENHANDS_BOOTSTRAP_DIRNAME = ".skeleton-openhands-bootstrap"
_OPENHANDS_PERSISTENCE_DIRNAME = ".openhands-secondary"

# OpenHands CLI's --override-with-envs currently overrides key/model/base URL,
# not the SDK LLM max_output_tokens field. Keep the ordinary OpenHands command
# contract and inject a code-owned, one-process Python startup hook from the
# Runner-owned private bookkeeping HOME. The hook persists the default Agent
# with the exact RouteLease output-token cap before the CLI loads its settings,
# verifies both agent and condenser caps, then removes PYTHONPATH so tools
# spawned by OpenHands do not inherit the bootstrap path.
_OPENHANDS_SITECUSTOMIZE = """\
import os


def _bounded_openhands_bootstrap_fail():
    os._exit(78)


if os.environ.get("SKELETON_OPENHANDS_BOOTSTRAP_REQUIRED") == "1":
    raw_limit = os.environ.get("SKELETON_OPENHANDS_MAX_OUTPUT_TOKENS", "")
    try:
        limit = int(raw_limit)
    except ValueError:
        _bounded_openhands_bootstrap_fail()
    if limit <= 0:
        _bounded_openhands_bootstrap_fail()
    model = os.environ.get("LLM_MODEL", "")
    persistence_dir = os.environ.get("OPENHANDS_PERSISTENCE_DIR", "")
    if not model or not persistence_dir:
        _bounded_openhands_bootstrap_fail()
    try:
        from openhands.sdk import LLM
        from openhands_cli.stores.agent_store import AgentStore
        from openhands_cli.utils import get_default_cli_agent

        llm = LLM(
            model=model,
            api_key="skeleton-nonsecret-placeholder",
            max_output_tokens=limit,
            usage_id="agent",
        )
        store = AgentStore()
        store.save(get_default_cli_agent(llm))
        read_back = store.load_from_disk()
        if (
            read_back is None
            or read_back.llm.max_output_tokens != limit
            or read_back.condenser is None
            or read_back.condenser.llm.max_output_tokens != limit
        ):
            _bounded_openhands_bootstrap_fail()
    except BaseException:
        _bounded_openhands_bootstrap_fail()
    finally:
        os.environ.pop("PYTHONPATH", None)
        os.environ.pop("SKELETON_OPENHANDS_BOOTSTRAP_REQUIRED", None)
        os.environ.pop("SKELETON_OPENHANDS_MAX_OUTPUT_TOKENS", None)
        os.environ.pop("LLM_MAX_OUTPUT_TOKENS", None)
"""

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


def task_contract_allows_cloud_secondary(task_content: str) -> bool:
    """Permit cloud secondary execution only for typed public repository-edit tasks."""
    try:
        raw = yaml.safe_load(task_content)
    except yaml.YAMLError:
        return False
    if not isinstance(raw, Mapping):
        return False
    requested = raw.get("requested_capabilities", ())
    if not isinstance(requested, (list, tuple)):
        return False
    requested_capabilities = {str(item) for item in requested}
    if "repository_write" not in requested_capabilities:
        return False
    privacy = str(raw.get("privacy_boundary", "")).upper()
    if not privacy or "PRIVATE" in privacy:
        return False
    return "PUBLIC" in privacy


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
        max_tokens=_OPENHANDS_SECONDARY_MAX_OUTPUT_TOKENS,
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


def _prepare_openhands_private_bootstrap(private_home: str) -> str:
    home = Path(private_home)
    try:
        if home.is_symlink() or not home.is_dir():
            raise OSError("private home is not a directory")
        bootstrap_dir = home / _OPENHANDS_BOOTSTRAP_DIRNAME
        if bootstrap_dir.exists() and (
            bootstrap_dir.is_symlink() or not bootstrap_dir.is_dir()
        ):
            raise OSError("bootstrap target is unsafe")
        bootstrap_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(bootstrap_dir, 0o700)
        sitecustomize = bootstrap_dir / "sitecustomize.py"
        if sitecustomize.exists() and (
            sitecustomize.is_symlink() or not sitecustomize.is_file()
        ):
            raise OSError("bootstrap file target is unsafe")
        temporary = bootstrap_dir / ".sitecustomize.py.tmp"
        if temporary.exists() and (
            temporary.is_symlink() or not temporary.is_file()
        ):
            raise OSError("bootstrap temporary target is unsafe")
        temporary.write_text(_OPENHANDS_SITECUSTOMIZE, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, sitecustomize)
        os.chmod(sitecustomize, 0o600)
    except OSError as exc:
        raise CodegenRouteError("openhands_private_bootstrap_unavailable") from exc
    return str(bootstrap_dir)


def prepare_openhands_secondary_environment(
    *,
    authority_environment: Mapping[str, str] | None = None,
    base_environment: Mapping[str, str] | None = None,
    route: OpenHandsSecondaryRoute | None = None,
) -> tuple[dict[str, str], dict[str, object]]:
    selected = route or select_openhands_secondary_route()
    if selected.lease.max_tokens <= 0:
        raise CodegenRouteError("openhands_bounded_token_budget_required")
    authority = os.environ if authority_environment is None else authority_environment
    environment: MutableMapping[str, str] = dict(base_environment or {})
    private_home = environment.get("HOME", "").strip()
    if not private_home:
        raise CodegenRouteError("openhands_private_home_required")
    try:
        bind_registered_environment_credential(
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
    bootstrap_dir = _prepare_openhands_private_bootstrap(private_home)
    environment["LLM_API_KEY"] = api_key
    environment["LLM_MODEL"] = selected.runtime_model
    environment["LLM_MAX_OUTPUT_TOKENS"] = str(selected.lease.max_tokens)
    environment[_OPENHANDS_BOUND_MAX_OUTPUT_TOKENS_ENV] = str(selected.lease.max_tokens)
    environment[_OPENHANDS_PERSISTENCE_DIR_ENV] = str(
        Path(private_home) / _OPENHANDS_PERSISTENCE_DIRNAME
    )
    environment[_OPENHANDS_BOOTSTRAP_REQUIRED_ENV] = "1"
    environment["PYTHONPATH"] = bootstrap_dir
    environment["MAX_BUDGET_PER_TASK"] = "0.50"
    environment["MAX_ITERATIONS"] = "20"
    environment["LLM_NUM_RETRIES"] = "1"
    public_receipt = {
        "executor_id": selected.binding.executor_id,
        "model_id": selected.binding.model_id,
        "binding_id": selected.binding.binding_id,
        "lease_hash": selected.lease.lease_hash,
        "max_output_tokens": selected.lease.max_tokens,
        "token_bound_transport": "private_startup_agent_config",
        "credential_status": "USED",
    }
    return dict(environment), public_receipt


def openhands_secondary_command(
    task_content: str, *, executable: str = "openhands"
) -> list[str]:
    return [
        executable,
        "--headless",
        "--json",
        "--override-with-envs",
        "-t",
        task_content,
    ]
