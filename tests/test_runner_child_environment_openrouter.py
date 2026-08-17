from __future__ import annotations

import os
from pathlib import Path
import subprocess

import core.runner_child_environment as child_env
from core.runner_child_environment import sanitize_codegen_child_environment


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


def test_child_environment_has_no_implicit_openrouter_binding() -> None:
    source = Path(child_env.__file__).read_text(encoding="utf-8")
    assert not hasattr(child_env, "_bind_trusted_openrouter")
    assert "bind_registered_environment_credential" not in source
    assert "runner-openhands" not in source
    assert "bind-openrouter-fallback" not in source


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


def _run_quota_wrapper(
    tmp_path: Path,
    *,
    include_fallback_key: bool,
) -> tuple[subprocess.CompletedProcess[str], Path]:
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
        "printf '%s\\n' called > \"$OPENHANDS_MARKER\"\n"
        "exit 0\n",
    )
    _write_executable(wrapper, child_env._WRAPPER)

    environment = dict(os.environ)
    environment.update(
        {
            "PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_REAL_CODEX_BIN": str(codex),
            "SKELETON_CODEGEN_ORIGINAL_PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_OPENHANDS_BIN": str(openhands),
            "SKELETON_OPENHANDS_OPENROUTER_REQUIRED": "1",
            "SKELETON_OPENROUTER_FALLBACK_MODEL": "openrouter/synthetic",
            "OPENROUTER_API_KEY": "must-not-leak",
            "BWS_ACCESS_TOKEN": "must-not-leak",
            "CREDENTIALS_DIRECTORY": "/must/not/leak",
            "OPENHANDS_MARKER": str(marker),
        }
    )
    if include_fallback_key:
        environment["SKELETON_OPENROUTER_FALLBACK_API_KEY"] = "synthetic-openrouter-key"

    result = subprocess.run(
        [str(wrapper), "exec", "--cd", str(workdir), "-"],
        input="synthetic bounded task",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )
    return result, marker


def test_wrapper_never_exposes_openrouter_key_or_calls_openhands_on_quota(tmp_path: Path) -> None:
    result, marker = _run_quota_wrapper(tmp_path, include_fallback_key=True)
    assert result.returncode == 1
    assert "usage limit reached" in result.stderr
    assert "synthetic-openrouter-key" not in result.stdout
    assert "synthetic-openrouter-key" not in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert "RESULT: OK" not in result.stdout
    assert not marker.exists()


def test_wrapper_ignores_obsolete_openrouter_required_toggle_and_preserves_codex_failure(tmp_path: Path) -> None:
    result, marker = _run_quota_wrapper(tmp_path, include_fallback_key=False)
    assert result.returncode == 1
    assert "usage limit reached" in result.stderr
    assert "SKELETON_CODEGEN_FALLBACK_CONFIG_UNAVAILABLE" not in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert not marker.exists()
