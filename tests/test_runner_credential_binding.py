from __future__ import annotations

from pathlib import Path

import core.runner_child_environment as child_env


def test_runner_openrouter_binding_uses_registered_credential_runtime(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_bind(**kwargs):
        observed.update(kwargs)
        kwargs["environment"]["SKELETON_OPENROUTER_FALLBACK_API_KEY"] = "synthetic"
        return {"result": {"status": "USED"}}

    monkeypatch.setattr(
        child_env,
        "bind_registered_environment_credential",
        fake_bind,
    )
    environment = {
        "BWS_ACCESS_TOKEN": "caller-must-not-win",
        "OPENROUTER_API_KEY": "caller-must-not-win",
        "UNRELATED": "keep",
    }
    authority = {"HOME": "/trusted", "PATH": "/trusted/bin"}

    assert child_env._bind_trusted_openrouter(environment, authority) is True
    assert observed["service_id"] == "runner-openhands"
    assert observed["alias"] == "openrouter-api"
    assert observed["action_id"] == "bind-openrouter-fallback"
    assert observed["authority_environment"] is authority
    assert "BWS_ACCESS_TOKEN" not in observed["environment"]
    assert "OPENROUTER_API_KEY" not in observed["environment"]
    assert environment["SKELETON_OPENROUTER_FALLBACK_API_KEY"] == "synthetic"
    assert environment["SKELETON_OPENROUTER_FALLBACK_MODEL"].startswith("openrouter/")
    assert environment["UNRELATED"] == "keep"


def test_runner_consumer_has_no_direct_bitwarden_or_secretstore_resolution_imports() -> None:
    source = Path(child_env.__file__).read_text(encoding="utf-8")
    assert "BwsCliSecretsManagerStore" not in source
    assert "bitwarden_reference_from_systemd_credential" not in source
    assert "SecretStoreGate" not in source
    assert "SecretAccessPolicy" not in source


def test_registered_credential_failure_keeps_openhands_binding_fail_closed(monkeypatch) -> None:
    def fail(**_kwargs):
        raise child_env.RegisteredCredentialRuntimeError("synthetic-failure")

    monkeypatch.setattr(child_env, "bind_registered_environment_credential", fail)
    environment = {"UNRELATED": "keep"}

    assert child_env._bind_trusted_openrouter(environment, {}) is False
    assert "SKELETON_OPENROUTER_FALLBACK_API_KEY" not in environment
    assert "SKELETON_OPENROUTER_FALLBACK_MODEL" not in environment
    assert environment["UNRELATED"] == "keep"
