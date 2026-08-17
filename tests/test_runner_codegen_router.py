from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

import core.runner_codegen_router as router
from core.runner_codegen_router import (
    CodegenRouteError,
    codex_failure_allows_secondary,
    openhands_secondary_command,
    prepare_openhands_secondary_environment,
    select_openhands_secondary_route,
)


ROOT = Path(__file__).resolve().parents[1]


def test_only_availability_failures_allow_secondary() -> None:
    assert codex_failure_allows_secondary(1, "usage limit reached")
    assert codex_failure_allows_secondary(1, "provider unavailable")
    assert not codex_failure_allows_secondary(0, "usage limit reached")
    assert not codex_failure_allows_secondary(1, "tests failed")
    assert not codex_failure_allows_secondary(1, "validation failed")


def test_production_route_selects_openhands_with_canary_passed_kimi() -> None:
    route = select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))
    assert route.binding.executor_id == "openhands-external"
    assert route.binding.model_id == "openrouter-kimi-k2-challenger"
    assert route.runtime_model == "openrouter/moonshotai/kimi-k2"
    assert route.lease.binding_id == route.binding.binding_id


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
) -> None:
    route = select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))

    def fake_bind(**kwargs):
        environment = kwargs["environment"]
        environment["SKELETON_OPENROUTER_FALLBACK_API_KEY"] = "synthetic-secret-marker"
        return {"result": {"status": "USED"}}

    monkeypatch.setattr(router, "bind_registered_environment_credential", fake_bind)
    environment, receipt = prepare_openhands_secondary_environment(
        authority_environment={"CREDENTIALS_DIRECTORY": "/synthetic"},
        base_environment={"PATH": "/usr/bin"},
        route=route,
    )

    assert environment["LLM_API_KEY"] == "synthetic-secret-marker"
    assert environment["LLM_MODEL"] == "openrouter/moonshotai/kimi-k2"
    assert "SKELETON_OPENROUTER_FALLBACK_API_KEY" not in environment
    assert "synthetic-secret-marker" not in json.dumps(receipt, sort_keys=True)
    assert receipt["executor_id"] == "openhands-external"
    assert receipt["model_id"] == "openrouter-kimi-k2-challenger"


def test_missing_registered_credential_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    route = select_openhands_secondary_route(now=datetime(2026, 8, 17, tzinfo=UTC))

    def fake_bind(**kwargs):
        return {"result": {"status": "FAILED"}}

    monkeypatch.setattr(router, "bind_registered_environment_credential", fake_bind)
    with pytest.raises(CodegenRouteError, match="openhands_registered_credential_unavailable"):
        prepare_openhands_secondary_environment(
            authority_environment={},
            base_environment={"PATH": "/usr/bin"},
            route=route,
        )


def test_openhands_command_is_fixed_except_task_text() -> None:
    command = openhands_secondary_command("bounded task")
    assert command == ["openhands", "--headless", "--json", "-t", "bounded task"]
    assert "moonshot" not in " ".join(command)
    assert "openrouter" not in " ".join(command)
