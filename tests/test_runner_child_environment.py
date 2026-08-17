from __future__ import annotations

import os
from pathlib import Path
import subprocess

import core.runner_child_environment as child_env
from core.runner_child_environment import sanitize_codegen_child_environment


def test_sanitize_codegen_child_environment_removes_home_edge_and_provider_authority(monkeypatch) -> None:
    environment = {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "SKELETON_HOME_EDGE_01_HOSTNAME": "live-home-edge",
        "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET": "synthetic-hmac-marker",
        "SKELETON_UNRELATED_SETTING": "kept-skeleton-value",
        "SKELETON_RUNNER_MEMORY_DB": "/private/runner.sqlite",
        "SKELETON_TG_BOT": "telegram-token",
        "OPENROUTER_API_KEY": "must-not-survive",
        "SKELETON_OPENROUTER_FALLBACK_API_KEY": "must-not-survive",
        "SKELETON_OPENROUTER_FALLBACK_MODEL": "openrouter/anything",
        "LLM_MODEL": "caller-model",
    }
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "_install_fallback_wrapper", lambda _env, _authority: None)
    sanitized = sanitize_codegen_child_environment(environment, authority_environment=environment)
    assert sanitized == {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "SKELETON_UNRELATED_SETTING": "kept-skeleton-value",
        "SKELETON_RUNNER_MEMORY_DB": "/private/runner.sqlite",
        "SKELETON_TG_BOT": "telegram-token",
    }


def _enable_recovered_runtime(monkeypatch, codex: Path) -> None:
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "is_canonical_systemd_runner_context", lambda _env: True)
    monkeypatch.setattr(child_env, "pinned_codex_recovery_marker_present", lambda _env: True)
    monkeypatch.setattr(child_env, "pinned_codex_runtime_path", lambda _env: str(codex))


def test_canonical_codegen_wrapper_binds_only_pinned_codex(tmp_path: Path, monkeypatch) -> None:
    codex = tmp_path / "codex-pinned"
    codex.write_text("", encoding="utf-8")
    authority = {"HOME": str(tmp_path), "PATH": "/trusted/bin", "INVOCATION_ID": "trusted"}
    _enable_recovered_runtime(monkeypatch, codex)

    sanitized = sanitize_codegen_child_environment(
        {
            "HOME": "/overlay/home",
            "PATH": "/overlay/bin",
            "SKELETON_OPENROUTER_FALLBACK_API_KEY": "caller-secret",
            "SKELETON_OPENROUTER_FALLBACK_MODEL": "openrouter/caller-model",
        },
        authority_environment=authority,
    )

    wrapper = tmp_path / ".local" / "state" / "skeleton-runner" / "codegen-fallback-bin" / "codex"
    assert wrapper.is_file()
    assert sanitized["HOME"] == str(tmp_path)
    assert sanitized["SKELETON_REAL_CODEX_BIN"] == str(codex.resolve())
    assert sanitized["SKELETON_OPENHANDS_BIN"] == ""
    assert sanitized["PATH"] == f"{wrapper.parent}:/trusted/bin"
    assert "SKELETON_OPENROUTER_FALLBACK_API_KEY" not in sanitized
    assert "SKELETON_OPENROUTER_FALLBACK_MODEL" not in sanitized


def test_validation_worktree_never_binds_live_wrapper(tmp_path: Path, monkeypatch) -> None:
    authority = {"HOME": str(tmp_path), "PATH": "/trusted/bin", "INVOCATION_ID": "live-systemd-marker"}
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "is_canonical_systemd_runner_context", lambda _env: False)
    monkeypatch.setattr(
        child_env,
        "pinned_codex_recovery_marker_present",
        lambda _env: (_ for _ in ()).throw(AssertionError("worktree must not inspect live marker")),
    )
    sanitized = sanitize_codegen_child_environment(
        {"HOME": "/overlay/home", "PATH": "/overlay/bin"},
        authority_environment=authority,
    )
    assert sanitized == {"HOME": "/overlay/home", "PATH": "/overlay/bin"}
    assert not (tmp_path / ".local" / "state" / "skeleton-runner").exists()


def test_wrapper_is_not_installed_before_recovery_marker(tmp_path: Path, monkeypatch) -> None:
    authority = {"HOME": str(tmp_path), "PATH": "/trusted/bin"}
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "is_canonical_systemd_runner_context", lambda _env: True)
    monkeypatch.setattr(child_env, "pinned_codex_recovery_marker_present", lambda _env: False)
    sanitized = sanitize_codegen_child_environment(
        {"HOME": str(tmp_path), "PATH": "/stale/bin"}, authority_environment=authority
    )
    assert sanitized["PATH"] == "/stale/bin"
    assert "SKELETON_REAL_CODEX_BIN" not in sanitized


def test_wrapper_is_not_installed_when_runtime_verification_fails(tmp_path: Path, monkeypatch) -> None:
    authority = {"HOME": str(tmp_path), "PATH": "/trusted/bin"}
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "is_canonical_systemd_runner_context", lambda _env: True)
    monkeypatch.setattr(child_env, "pinned_codex_recovery_marker_present", lambda _env: True)
    monkeypatch.setattr(
        child_env,
        "pinned_codex_runtime_path",
        lambda _env: (_ for _ in ()).throw(child_env.CodexRuntimeRecoveryError("version_mismatch")),
    )
    sanitized = sanitize_codegen_child_environment(
        {"HOME": str(tmp_path), "PATH": "/stale/bin"}, authority_environment=authority
    )
    assert sanitized["PATH"] == "/stale/bin"
    assert "SKELETON_REAL_CODEX_BIN" not in sanitized


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


def _run_wrapper(tmp_path: Path, *, codex_body: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    codex = bin_dir / "codex-real"
    openhands = bin_dir / "openhands-real"
    wrapper = bin_dir / "codex"
    fallback_marker = tmp_path / "openhands-called"
    _write_executable(codex, codex_body)
    _write_executable(openhands, "#!/bin/sh\nprintf '%s\\n' called > \"$OPENHANDS_MARKER\"\nexit 0\n")
    _write_executable(wrapper, child_env._WRAPPER)
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_REAL_CODEX_BIN": str(codex),
            "SKELETON_OPENHANDS_BIN": str(openhands),
            "SKELETON_CODEGEN_ORIGINAL_PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_OPENROUTER_FALLBACK_API_KEY": "synthetic-secret",
            "SKELETON_OPENROUTER_FALLBACK_MODEL": "openrouter/synthetic-model",
            "OPENHANDS_MARKER": str(fallback_marker),
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
    return result, fallback_marker


def test_quota_failure_does_not_implicitly_invoke_openhands(tmp_path: Path) -> None:
    result, fallback_marker = _run_wrapper(
        tmp_path,
        codex_body="#!/bin/sh\nprintf '%s\\n' 'usage limit reached' >&2\nexit 1\n",
    )
    assert result.returncode == 1
    assert "usage limit reached" in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert "RESULT: OK" not in result.stdout
    assert not fallback_marker.exists()


def test_unrelated_codex_failure_still_propagates(tmp_path: Path) -> None:
    result, fallback_marker = _run_wrapper(
        tmp_path,
        codex_body="#!/bin/sh\nprintf '%s\\n' 'unrelated synthetic codex failure' >&2\nexit 7\n",
    )
    assert result.returncode == 7
    assert "unrelated synthetic codex failure" in result.stderr
    assert not fallback_marker.exists()


def test_successful_codex_still_reports_codex_provider(tmp_path: Path) -> None:
    result, fallback_marker = _run_wrapper(
        tmp_path,
        codex_body="#!/bin/sh\nprintf '%s\\n' 'completed'\nexit 0\n",
    )
    assert result.returncode == 0
    assert "SKELETON_CODEGEN_PROVIDER=codex" in result.stdout
    assert "completed" in result.stdout
    assert not fallback_marker.exists()
