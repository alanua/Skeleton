from __future__ import annotations

import os
from pathlib import Path
import subprocess

import core.runner_child_environment as child_env
from core.secret_store import ResolvedSecret, SecretReference
from core.runner_child_environment import sanitize_codegen_child_environment


class FakeBitwardenStore:
    provider = "bitwarden"

    def resolve(self, reference, context):
        assert reference == SecretReference(provider="bitwarden", reference_id="11111111-2222-3333-4444-555555555555")
        assert context.machine_identity == "hetzner-agent-runner-1"
        assert context.audience == "openhands-openrouter"
        assert context.task_kind == "code_generation"
        return ResolvedSecret("synthetic-openrouter-key")


def test_codegen_child_environment_scrubs_all_secret_sources(monkeypatch) -> None:
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "_install_fallback_wrapper", lambda _env, _authority: None)
    environment = {
        "PATH": "/usr/bin",
        "OPENROUTER_API_KEY": "must-not-reach-codex",
        "BWS_ACCESS_TOKEN": "must-not-reach-codex",
        "CREDENTIALS_DIRECTORY": "/run/credentials/private",
        "LLM_API_KEY": "overlay-key",
        "LLM_MODEL": "overlay-model",
        "MAX_BUDGET_PER_TASK": "999",
        "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET": "also-scrubbed",
        "SAFE_SETTING": "kept",
    }

    sanitized = sanitize_codegen_child_environment(environment, authority_environment=environment)

    assert sanitized == {"PATH": "/usr/bin", "SAFE_SETTING": "kept"}
    assert environment["OPENROUTER_API_KEY"] == "must-not-reach-codex"
    assert environment["BWS_ACCESS_TOKEN"] == "must-not-reach-codex"


def test_trusted_openrouter_binding_uses_exact_bitwarden_reference_and_code_owned_policy(monkeypatch) -> None:
    reference = SecretReference(provider="bitwarden", reference_id="11111111-2222-3333-4444-555555555555")
    monkeypatch.setattr(
        child_env,
        "bitwarden_reference_from_systemd_credential",
        lambda authority, name: reference,
    )
    monkeypatch.setattr(
        child_env.BwsCliSecretsManagerStore,
        "from_systemd_credentials",
        classmethod(lambda cls, authority: FakeBitwardenStore()),
    )
    environment = {
        "SAFE": "1",
        "OPENROUTER_API_KEY": "overlay-secret",
        "LLM_MODEL": "attacker/model",
    }

    assert child_env._bind_trusted_openrouter(environment, {"PATH": "/trusted/bin"}) is True
    assert environment["SAFE"] == "1"
    assert environment["SKELETON_OPENROUTER_FALLBACK_API_KEY"] == "synthetic-openrouter-key"
    assert environment["SKELETON_OPENROUTER_FALLBACK_MODEL"] == "openrouter/z-ai/glm-4.5-air:free"
    assert "OPENROUTER_API_KEY" not in environment
    assert "LLM_MODEL" not in environment


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


def test_wrapper_exposes_openrouter_key_only_to_openhands_fallback(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    codex = bin_dir / "codex-real"
    openhands = bin_dir / "openhands-real"
    wrapper = bin_dir / "codex"
    marker = tmp_path / "openhands-called"
    _write_executable(
        codex,
        "#!/bin/sh\n"
        "test -z \"${LLM_API_KEY:-}\" || exit 21\n"
        "test -z \"${OPENROUTER_API_KEY:-}\" || exit 22\n"
        "test -z \"${BWS_ACCESS_TOKEN:-}\" || exit 23\n"
        "test -z \"${CREDENTIALS_DIRECTORY:-}\" || exit 24\n"
        "printf '%s\\n' 'usage limit reached' >&2\n"
        "exit 1\n",
    )
    _write_executable(
        openhands,
        "#!/bin/sh\n"
        "test \"${LLM_API_KEY:-}\" = 'synthetic-openrouter-key' || exit 31\n"
        "test \"${LLM_MODEL:-}\" = 'openrouter/z-ai/glm-4.5-air:free' || exit 32\n"
        "test -z \"${BWS_ACCESS_TOKEN:-}\" || exit 33\n"
        "test -z \"${CREDENTIALS_DIRECTORY:-}\" || exit 34\n"
        "test -z \"${OPENROUTER_API_KEY:-}\" || exit 35\n"
        "printf '%s\\n' called > \"$OPENHANDS_MARKER\"\n"
        "exit 0\n",
    )
    _write_executable(wrapper, child_env._WRAPPER)
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_REAL_CODEX_BIN": str(codex),
            "SKELETON_OPENHANDS_BIN": str(openhands),
            "SKELETON_CODEGEN_ORIGINAL_PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_OPENHANDS_OPENROUTER_REQUIRED": "1",
            "SKELETON_OPENROUTER_FALLBACK_API_KEY": "synthetic-openrouter-key",
            "SKELETON_OPENROUTER_FALLBACK_MODEL": "openrouter/z-ai/glm-4.5-air:free",
            "OPENROUTER_API_KEY": "must-not-leak",
            "BWS_ACCESS_TOKEN": "must-not-leak",
            "CREDENTIALS_DIRECTORY": "/must/not/leak",
            "OPENHANDS_MARKER": str(marker),
        }
    )

    result = subprocess.run(
        [str(wrapper), "exec", "--cd", str(workdir), "-"],
        input="synthetic bounded task",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )

    assert result.returncode == 0
    assert marker.read_text(encoding="utf-8").strip() == "called"
    assert "synthetic-openrouter-key" not in result.stdout
    assert "synthetic-openrouter-key" not in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" in result.stdout


def test_wrapper_fails_closed_when_openrouter_is_required_but_unavailable(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    codex = bin_dir / "codex-real"
    openhands = bin_dir / "openhands-real"
    wrapper = bin_dir / "codex"
    marker = tmp_path / "openhands-called"
    _write_executable(codex, "#!/bin/sh\nprintf '%s\\n' 'usage limit reached' >&2\nexit 1\n")
    _write_executable(openhands, "#!/bin/sh\nprintf '%s\\n' called > \"$OPENHANDS_MARKER\"\nexit 0\n")
    _write_executable(wrapper, child_env._WRAPPER)
    environment = dict(os.environ)
    environment.update(
        {
            "SKELETON_REAL_CODEX_BIN": str(codex),
            "SKELETON_OPENHANDS_BIN": str(openhands),
            "SKELETON_CODEGEN_ORIGINAL_PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_OPENHANDS_OPENROUTER_REQUIRED": "1",
            "OPENHANDS_MARKER": str(marker),
        }
    )

    result = subprocess.run(
        [str(wrapper), "exec", "--cd", str(workdir), "-"],
        input="synthetic bounded task",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )

    assert result.returncode == 1
    assert "SKELETON_CODEGEN_FALLBACK_CONFIG_UNAVAILABLE" in result.stderr
    assert not marker.exists()
