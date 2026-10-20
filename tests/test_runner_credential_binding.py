from __future__ import annotations

from pathlib import Path

import core.runner_child_environment as child_env
from core.runner_child_environment import sanitize_codegen_child_environment
from scripts import runner_openai_reference_bootstrap as bootstrap


def test_runner_child_environment_binds_only_registered_primary_codex_credential() -> None:
    source = Path(child_env.__file__).read_text(encoding="utf-8")
    assert not hasattr(child_env, "_bind_trusted_openrouter")
    assert "runner-openhands" not in source
    assert "bind-openrouter-fallback" not in source
    assert "runner-codex-openai" in source
    assert "bind-openai-api-key" in source


def test_runner_consumer_has_no_direct_bitwarden_or_secretstore_resolution_imports() -> None:
    source = Path(child_env.__file__).read_text(encoding="utf-8")
    assert "BwsCliSecretsManagerStore" not in source
    assert "bitwarden_reference_from_systemd_credential" not in source
    assert "SecretStoreGate" not in source
    assert "SecretAccessPolicy" not in source


def test_sanitize_strips_provider_credentials_without_resolving_them(monkeypatch) -> None:
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "_install_fallback_wrapper", lambda _env, _authority: None)
    environment = {
        "HOME": "/overlay/home",
        "PATH": "/overlay/bin",
        "BWS_ACCESS_TOKEN": "caller-must-not-win",
        "OPENROUTER_API_KEY": "caller-must-not-win",
        "OPENAI_API_KEY": "caller-must-not-win",
        "LLM_API_KEY": "caller-must-not-win",
        "LLM_MODEL": "attacker/model",
        "SKELETON_OPENHANDS_BIN": "/untrusted/openhands",
        "SKELETON_OPENROUTER_FALLBACK_API_KEY": "caller-must-not-win",
        "SKELETON_OPENROUTER_FALLBACK_MODEL": "attacker/model",
        "UNRELATED": "keep",
    }

    sanitized = sanitize_codegen_child_environment(
        environment,
        authority_environment={"HOME": "/trusted", "PATH": "/trusted/bin"},
    )

    assert sanitized == {
        "HOME": "/overlay/home",
        "PATH": "/overlay/bin",
        "UNRELATED": "keep",
    }


def test_primary_codex_preparation_sanitizes_before_registered_binding(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "_install_fallback_wrapper", lambda _env, _authority: None)

    def bind(**kwargs) -> None:
        environment = kwargs["environment"]
        calls.append((kwargs["service_id"], dict(environment)))
        environment["OPENAI_API_KEY"] = "trusted-synthetic-value"

    monkeypatch.setattr(child_env, "bind_registered_environment_credential", bind)
    prepared = child_env.prepare_primary_codex_environment(
        {"OPENAI_API_KEY": "caller-value", "UNRELATED": "keep"},
        authority_environment={
            child_env.CODEX_AUTH_MODE_ENV: child_env.CODEX_AUTH_MODE_OPENAI_API
        },
    )

    assert calls == [("runner-codex-openai", {"UNRELATED": "keep"})]
    assert prepared == {
        "OPENAI_API_KEY": "trusted-synthetic-value",
        "UNRELATED": "keep",
    }


def test_primary_codex_preparation_defaults_to_chatgpt_without_api_binding(monkeypatch) -> None:
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "_install_fallback_wrapper", lambda _env, _authority: None)
    monkeypatch.setattr(
        child_env,
        "bind_registered_environment_credential",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not bind in chatgpt mode")
        ),
    )

    prepared = child_env.prepare_primary_codex_environment(
        {"OPENAI_API_KEY": "caller-value", "UNRELATED": "keep"},
        authority_environment={},
    )

    assert prepared == {"UNRELATED": "keep"}


def test_openai_reference_bootstrap_preserves_unrelated_registrations() -> None:
    unrelated = {
        "service_id": "mail-gmail",
        "alias": "acct:gmail-primary",
        "provider": "bitwarden",
        "reference_id": "11111111-2222-3333-4444-555555555555",
    }
    old_target = {
        "service_id": bootstrap.SERVICE_ID,
        "alias": bootstrap.ALIAS,
        "provider": bootstrap.PROVIDER,
        "reference_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    }

    merged = bootstrap._merged_index(
        {"schema": bootstrap.SCHEMA, "registrations": [unrelated, old_target]},
        "99999999-8888-7777-6666-555555555555",
    )

    assert merged["registrations"] == [
        unrelated,
        {
            "service_id": "runner-codex-openai",
            "alias": "api-key",
            "provider": "bitwarden",
            "reference_id": "99999999-8888-7777-6666-555555555555",
        },
    ]
