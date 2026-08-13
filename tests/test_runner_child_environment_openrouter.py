from __future__ import annotations

import os
import subprocess
import sys

import pytest

import core.runner_child_environment as child_env
from core.runner_child_environment import (
    openhands_openrouter_execution_environment,
    sanitize_codegen_child_environment,
)
from core.secret_store import (
    SecretMissing,
    SecretRecord,
    SecretReference,
    SecretResolutionContext,
    SecretStatus,
    SecretStoreGate,
)


class FakeOpenRouterStore:
    provider = "bitwarden"

    def __init__(self, *, value: str | None = "synthetic-openrouter-token") -> None:
        self.value = value
        self.calls: list[tuple[SecretReference, SecretResolutionContext]] = []

    def read(self, reference: SecretReference, context: SecretResolutionContext) -> SecretRecord:
        self.calls.append((reference, context))
        if self.value is None:
            raise SecretMissing("missing")
        return SecretRecord(
            reference=reference,
            value=self.value,
            status=SecretStatus.ACTIVE,
            allowed_machine_identities=frozenset({context.machine_identity}),
            allowed_audiences=frozenset({context.audience}),
            allowed_task_kinds=frozenset({context.task_kind}),
        )


def _reference() -> SecretReference:
    return SecretReference(provider="bitwarden", reference_id="openhands/openrouter/api-key", version="ver1")


def test_codex_child_environment_never_receives_openrouter_secret_sources(monkeypatch) -> None:
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "_install_fallback_wrapper", lambda _env, _authority: None)
    environment = {
        "PATH": "/usr/bin",
        "OPENROUTER_API_KEY": "must-not-reach-codex",
        "CREDENTIALS_DIRECTORY": "/run/credentials/private",
        "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET": "also-scrubbed",
        "SAFE_SETTING": "kept",
    }

    sanitized = sanitize_codegen_child_environment(environment, authority_environment=environment)

    assert sanitized == {"PATH": "/usr/bin", "SAFE_SETTING": "kept"}
    assert environment["OPENROUTER_API_KEY"] == "must-not-reach-codex"


def test_openhands_child_receives_secret_only_after_gate_resolution() -> None:
    store = FakeOpenRouterStore()
    environment = {
        "PATH": "/usr/bin",
        "OPENROUTER_API_KEY": "overlay-secret",
        "CREDENTIALS_DIRECTORY": "/run/credentials/private",
        "OPENROUTER_MODEL": "attacker/model",
        "OPENROUTER_BUDGET_USD": "9999",
        "OPENROUTER_MAX_ITERATIONS": "999",
    }

    child = openhands_openrouter_execution_environment(
        environment,
        secret_reference=_reference(),
        secret_store_gate=SecretStoreGate({"bitwarden": store}),
        machine_identity="runner-host-01",
        audience="openhands",
        task_kind="repair-pr",
    )

    assert child["OPENROUTER_API_KEY"] == "synthetic-openrouter-token"
    assert child["OPENROUTER_MODEL"] == "anthropic/claude-sonnet-4"
    assert child["OPENROUTER_BUDGET_USD"] == "0.50"
    assert child["OPENROUTER_MAX_ITERATIONS"] == "12"
    assert child["OPENROUTER_MAX_RETRIES"] == "2"
    assert child["OPENROUTER_HTTP_TIMEOUT_SECONDS"] == "60"
    assert "CREDENTIALS_DIRECTORY" not in child
    assert store.calls == [
        (
            _reference(),
            SecretResolutionContext(
                machine_identity="runner-host-01",
                audience="openhands",
                task_kind="repair-pr",
            ),
        )
    ]


def test_synthetic_fake_provider_process_injection_does_not_leak_to_parent_or_output() -> None:
    store = FakeOpenRouterStore()
    child = openhands_openrouter_execution_environment(
        {"PATH": os.environ.get("PATH", "")},
        secret_reference=_reference(),
        secret_store_gate=SecretStoreGate({"bitwarden": store}),
        machine_identity="runner-host-01",
        audience="openhands",
        task_kind="repair-pr",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; assert os.environ['OPENROUTER_API_KEY']; print(os.environ['OPENROUTER_MODEL'])",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "anthropic/claude-sonnet-4"
    assert "synthetic-openrouter-token" not in result.stdout
    assert "synthetic-openrouter-token" not in result.stderr
    assert os.environ.get("OPENROUTER_API_KEY") != "synthetic-openrouter-token"


def test_missing_secret_fails_closed_before_openhands_env_is_returned() -> None:
    with pytest.raises(SecretMissing):
        openhands_openrouter_execution_environment(
            {"PATH": "/usr/bin"},
            secret_reference=_reference(),
            secret_store_gate=SecretStoreGate({"bitwarden": FakeOpenRouterStore(value=None)}),
            machine_identity="runner-host-01",
            audience="openhands",
            task_kind="repair-pr",
        )
