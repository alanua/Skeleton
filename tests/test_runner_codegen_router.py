from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path

import pytest

import core.runner_codegen_router as router
from core.runner_codegen_router import (
    CodegenRouteError,
    codex_failure_allows_secondary,
    openhands_secondary_command,
    prepare_openhands_secondary_environment,
    select_openhands_secondary_route,
    task_contract_allows_cloud_secondary,
)


ROOT = Path(__file__).resolve().parents[1]


def test_only_availability_failures_allow_secondary() -> None:
    assert codex_failure_allows_secondary(1, "usage limit reached")
    assert codex_failure_allows_secondary(1, "provider unavailable")
    assert not codex_failure_allows_secondary(0, "usage limit reached")
    assert not codex_failure_allows_secondary(1, "tests failed")
    assert not codex_failure_allows_secondary(1, "validation failed")


def test_cloud_secondary_requires_public_repository_write_contract() -> None:
    assert task_contract_allows_cloud_secondary(
        """
requested_capabilities: [repository_read, repository_write, test_execution]
privacy_boundary: PUBLIC_SAFE_REPOSITORY_ONLY
"""
    )
    assert not task_contract_allows_cloud_secondary(
        """
requested_capabilities: [repository_read]
privacy_boundary: PUBLIC_SAFE_READ_ONLY
"""
    )
    assert not task_contract_allows_cloud_secondary(
        """
requested_capabilities: [repository_read, repository_write]
privacy_boundary: PRIVATE_LOCAL_ONLY
"""
    )
    assert not task_contract_allows_cloud_secondary("not: [valid")


def test_production_route_selects_openhands_with_canary_passed_kimi() -> None:
    route = select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))
    assert route.binding.executor_id == "openhands-external"
    assert route.binding.model_id == "openrouter-kimi-k2-challenger"
    assert route.runtime_model == "openrouter/moonshotai/kimi-k2"
    assert route.lease.binding_id == route.binding.binding_id
    assert route.lease.max_tokens == 768


def test_glm_and_local_are_not_eligible_production_codegen_models() -> None:
    route = select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))
    assert route.binding.model_id not in {
        "openrouter-glm-free-challenger",
        "local-small",
    }


def test_unregistered_runtime_model_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(router, "_OPENHANDS_RUNTIME_MODEL_BY_MODEL_ID", {})
    with pytest.raises(CodegenRouteError, match="openhands_runtime_model_unregistered"):
        select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))


def test_registered_credential_is_bound_ephemerally_and_public_receipt_has_no_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    route = select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))

    def fake_bind(**kwargs):
        environment = kwargs["environment"]
        environment["SKELETON_OPENROUTER_FALLBACK_API_KEY"] = "synthetic-secret-marker"
        return {"result": {"status": "USED"}}

    private_home = tmp_path / "home"
    private_home.mkdir(mode=0o700)
    monkeypatch.setattr(router, "bind_registered_environment_credential", fake_bind)
    environment, receipt = prepare_openhands_secondary_environment(
        authority_environment={"CREDENTIALS_DIRECTORY": "/synthetic"},
        base_environment={"PATH": "/usr/bin", "HOME": str(private_home)},
        route=route,
    )

    assert environment["LLM_API_KEY"] == "synthetic-secret-marker"
    assert environment["LLM_MODEL"] == "openrouter/moonshotai/kimi-k2"
    assert environment["LLM_MAX_OUTPUT_TOKENS"] == "768"
    assert environment["SKELETON_OPENHANDS_MAX_OUTPUT_TOKENS"] == "768"
    assert environment["OPENHANDS_PERSISTENCE_DIR"] == str(
        private_home / ".openhands-secondary"
    )
    assert environment["SKELETON_OPENHANDS_BOOTSTRAP_REQUIRED"] == "1"
    bootstrap_dir = private_home / ".skeleton-openhands-bootstrap"
    assert environment["PYTHONPATH"] == str(bootstrap_dir)
    sitecustomize = bootstrap_dir / "sitecustomize.py"
    bootstrap = sitecustomize.read_text(encoding="utf-8")
    assert sitecustomize.stat().st_mode & 0o777 == 0o600
    assert "max_output_tokens=limit" in bootstrap
    assert "get_default_cli_agent" in bootstrap
    assert "read_back.llm.max_output_tokens != limit" in bootstrap
    assert "read_back.condenser.llm.max_output_tokens != limit" in bootstrap
    assert "os.environ.pop(\"PYTHONPATH\", None)" in bootstrap
    assert "synthetic-secret-marker" not in bootstrap
    assert int(environment["SKELETON_OPENHANDS_MAX_OUTPUT_TOKENS"]) < 100352
    assert "SKELETON_OPENROUTER_FALLBACK_API_KEY" not in environment
    assert "synthetic-secret-marker" not in json.dumps(receipt, sort_keys=True)
    assert receipt["executor_id"] == "openhands-external"
    assert receipt["model_id"] == "openrouter-kimi-k2-challenger"
    assert receipt["max_output_tokens"] == 768
    assert receipt["token_bound_transport"] == "private_startup_agent_config"


def test_secondary_requires_private_bookkeeping_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))
    monkeypatch.setattr(
        router,
        "bind_registered_environment_credential",
        lambda **kwargs: {"result": {"status": "USED"}},
    )
    with pytest.raises(CodegenRouteError, match="openhands_private_home_required"):
        prepare_openhands_secondary_environment(
            authority_environment={},
            base_environment={"PATH": "/usr/bin"},
            route=route,
        )


def test_secondary_rejects_unsafe_private_bootstrap_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    route = select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))

    def fake_bind(**kwargs):
        kwargs["environment"]["SKELETON_OPENROUTER_FALLBACK_API_KEY"] = "secret"
        return {"result": {"status": "USED"}}

    private_home = tmp_path / "home"
    private_home.mkdir(mode=0o700)
    (private_home / ".skeleton-openhands-bootstrap").symlink_to(tmp_path / "elsewhere")
    monkeypatch.setattr(router, "bind_registered_environment_credential", fake_bind)
    with pytest.raises(
        CodegenRouteError, match="openhands_private_bootstrap_unavailable"
    ):
        prepare_openhands_secondary_environment(
            authority_environment={},
            base_environment={"PATH": "/usr/bin", "HOME": str(private_home)},
            route=route,
        )


def test_secondary_requires_positive_code_owned_token_lease() -> None:
    route = select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))
    invalid = router.OpenHandsSecondaryRoute(
        binding=route.binding,
        lease=replace(route.lease, max_tokens=0),
        runtime_model=route.runtime_model,
    )
    with pytest.raises(CodegenRouteError, match="openhands_bounded_token_budget_required"):
        prepare_openhands_secondary_environment(
            authority_environment={},
            base_environment={"PATH": "/usr/bin"},
            route=invalid,
        )


def test_missing_registered_credential_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    route = select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))

    def fake_bind(**kwargs):
        return {"result": {"status": "FAILED"}}

    private_home = tmp_path / "home"
    private_home.mkdir(mode=0o700)
    monkeypatch.setattr(router, "bind_registered_environment_credential", fake_bind)
    with pytest.raises(CodegenRouteError, match="openhands_registered_credential_unavailable"):
        prepare_openhands_secondary_environment(
            authority_environment={},
            base_environment={"PATH": "/usr/bin", "HOME": str(private_home)},
            route=route,
        )


def test_openhands_command_is_fixed_except_task_text() -> None:
    command = openhands_secondary_command("bounded task")
    assert command == [
        "openhands",
        "--headless",
        "--json",
        "--override-with-envs",
        "-t",
        "bounded task",
    ]
    assert "moonshot" not in " ".join(command)
    assert "openrouter" not in " ".join(command)


def test_openhands_command_preserves_code_owned_resolved_executable() -> None:
    command = openhands_secondary_command(
        "bounded task", executable="/usr/bin/openhands"
    )
    assert command == [
        "/usr/bin/openhands",
        "--headless",
        "--json",
        "--override-with-envs",
        "-t",
        "bounded task",
    ]
