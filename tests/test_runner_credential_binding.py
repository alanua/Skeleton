from __future__ import annotations

from pathlib import Path

import core.runner_child_environment as child_env
from core.runner_child_environment import sanitize_codegen_child_environment


def test_runner_child_environment_has_no_implicit_openrouter_credential_runtime() -> None:
    source = Path(child_env.__file__).read_text(encoding="utf-8")
    assert not hasattr(child_env, "_bind_trusted_openrouter")
    assert "bind_registered_environment_credential" not in source
    assert "RegisteredCredentialRuntimeError" not in source
    assert "runner-openhands" not in source
    assert "bind-openrouter-fallback" not in source


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
