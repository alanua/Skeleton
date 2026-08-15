from __future__ import annotations

import os
from pathlib import Path
import subprocess

import core.runner_child_environment as child_env
from core.runner_child_environment import sanitize_codegen_child_environment


def _enable_runtime(monkeypatch, codex: Path, openhands: Path | None) -> None:
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "is_canonical_systemd_runner_context", lambda _env: True)
    monkeypatch.setattr(child_env, "pinned_codex_recovery_marker_present", lambda _env: True)
    monkeypatch.setattr(child_env, "pinned_codex_runtime_path", lambda _env: str(codex))
    monkeypatch.setattr(
        child_env.shutil,
        "which",
        lambda name, *, path=None: str(openhands) if name == "openhands" and openhands else None,
    )


def test_openrouter_binding_uses_only_trusted_authority_environment(
    tmp_path: Path, monkeypatch
) -> None:
    trusted_bin = tmp_path / "bin"
    trusted_bin.mkdir()
    codex = trusted_bin / "codex"
    openhands = trusted_bin / "openhands"
    codex.write_text("", encoding="utf-8")
    openhands.write_text("", encoding="utf-8")
    _enable_runtime(monkeypatch, codex, openhands)

    authority = {
        "HOME": str(tmp_path),
        "PATH": str(trusted_bin),
        "OPENROUTER_API_KEY": "synthetic-trusted-openrouter-key",
    }
    overlay = {
        "HOME": "/overlay/home",
        "PATH": "/overlay/bin",
        "OPENROUTER_API_KEY": "synthetic-overlay-key",
        "LLM_API_KEY": "synthetic-overlay-llm-key",
        "LLM_MODEL": "openrouter/attacker/model",
        "LLM_BASE_URL": "https://attacker.invalid",
        "MAX_BUDGET_PER_TASK": "999",
    }

    sanitized = sanitize_codegen_child_environment(
        overlay, authority_environment=authority
    )

    assert sanitized["SKELETON_OPENROUTER_FALLBACK_API_KEY"] == "synthetic-trusted-openrouter-key"
    assert sanitized["SKELETON_OPENROUTER_FALLBACK_MODEL"] == child_env._OPENROUTER_FREE_MODEL
    assert sanitized["SKELETON_OPENHANDS_OPENROUTER_REQUIRED"] == "1"
    assert sanitized["SKELETON_OPENHANDS_BIN"] == str(openhands.resolve())
    assert "OPENROUTER_API_KEY" not in sanitized
    assert "LLM_API_KEY" not in sanitized
    assert "LLM_MODEL" not in sanitized
    assert "LLM_BASE_URL" not in sanitized
    assert "MAX_BUDGET_PER_TASK" not in sanitized
    assert "attacker.invalid" not in repr(sanitized)


def test_openrouter_binding_reads_systemd_credential_directory(
    tmp_path: Path, monkeypatch
) -> None:
    trusted_bin = tmp_path / "bin"
    trusted_bin.mkdir()
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    credential = credentials / "openrouter-api-key"
    credential.write_text("synthetic-credential-key\n", encoding="utf-8")
    credential.chmod(0o600)
    codex = trusted_bin / "codex"
    openhands = trusted_bin / "openhands"
    codex.write_text("", encoding="utf-8")
    openhands.write_text("", encoding="utf-8")
    _enable_runtime(monkeypatch, codex, openhands)

    sanitized = sanitize_codegen_child_environment(
        {"HOME": str(tmp_path), "PATH": str(trusted_bin)},
        authority_environment={
            "HOME": str(tmp_path),
            "PATH": str(trusted_bin),
            "CREDENTIALS_DIRECTORY": str(credentials),
        },
    )

    assert sanitized["SKELETON_OPENROUTER_FALLBACK_API_KEY"] == "synthetic-credential-key"
    assert sanitized["SKELETON_OPENROUTER_FALLBACK_MODEL"] == child_env._OPENROUTER_FREE_MODEL
    assert sanitized["SKELETON_OPENHANDS_OPENROUTER_REQUIRED"] == "1"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


def _run_production_wrapper(
    tmp_path: Path, *, include_openrouter_config: bool
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    codex = bin_dir / "codex-real"
    openhands = bin_dir / "openhands-real"
    wrapper = bin_dir / "codex"
    marker = tmp_path / "openhands-argv"
    codex_env_marker = tmp_path / "codex-env"
    _write_executable(
        codex,
        "#!/bin/sh\n"
        "test -z \"${SKELETON_OPENROUTER_FALLBACK_API_KEY:-}\" || exit 21\n"
        "test -z \"${LLM_API_KEY:-}\" || exit 22\n"
        "printf '%s\\n' clean > \"$CODEX_ENV_MARKER\"\n"
        "printf '%s\\n' 'usage limit reached' >&2\n"
        "exit 1\n",
    )
    _write_executable(
        openhands,
        "#!/bin/sh\n"
        "test \"${LLM_MODEL:-}\" = 'openrouter/synthetic-model' || exit 9\n"
        "test \"${LLM_API_KEY:-}\" = 'synthetic-key' || exit 10\n"
        "test \"${MAX_BUDGET_PER_TASK:-}\" = '0.50' || exit 11\n"
        "test \"${MAX_ITERATIONS:-}\" = '20' || exit 12\n"
        "test \"${LLM_NUM_RETRIES:-}\" = '1' || exit 13\n"
        "printf '%s\\n' \"$@\" > \"$OPENHANDS_MARKER\"\n"
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
            "OPENHANDS_MARKER": str(marker),
            "CODEX_ENV_MARKER": str(codex_env_marker),
        }
    )
    if include_openrouter_config:
        environment.update(
            {
                "SKELETON_OPENROUTER_FALLBACK_MODEL": "openrouter/synthetic-model",
                "SKELETON_OPENROUTER_FALLBACK_API_KEY": "synthetic-key",
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
    assert codex_env_marker.read_text(encoding="utf-8").strip() == "clean"
    return result, marker


def test_production_wrapper_fails_closed_without_openrouter_configuration(
    tmp_path: Path,
) -> None:
    result, marker = _run_production_wrapper(
        tmp_path, include_openrouter_config=False
    )

    assert result.returncode == 1
    assert "SKELETON_CODEGEN_FALLBACK_CONFIG_UNAVAILABLE" in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert not marker.exists()


def test_production_wrapper_runs_openhands_with_environment_override_and_budget(
    tmp_path: Path,
) -> None:
    result, marker = _run_production_wrapper(
        tmp_path, include_openrouter_config=True
    )

    assert result.returncode == 0
    assert "SKELETON_CODEGEN_PROVIDER=openhands" in result.stdout
    assert "RESULT: OK" in result.stdout
    argv = marker.read_text(encoding="utf-8").splitlines()
    assert argv[:3] == ["--headless", "--json", "--override-with-envs"]
    assert "-t" in argv
