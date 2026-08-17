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
        "SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE": "/private/key",
        "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET": "synthetic-hmac-marker",
        "SKELETON_UNRELATED_SETTING": "kept-skeleton-value",
        "SKELETON_RUNNER_MEMORY_DB": "/private/runner.sqlite",
        "SKELETON_TG_BOT": "telegram-token",
        "UNRELATED_HOME_EDGE_01_VALUE": "kept",
        "ARBITRARY_OVERLAY_VALUE": "kept-overlay-value",
        "OPENROUTER_API_KEY": "must-not-survive",
        "SKELETON_OPENHANDS_BIN": "/untrusted/openhands",
        "SKELETON_REAL_CODEX_BIN": "/untrusted/codex",
        "SKELETON_CODEGEN_ORIGINAL_PATH": "/untrusted/path",
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
        "UNRELATED_HOME_EDGE_01_VALUE": "kept",
        "ARBITRARY_OVERLAY_VALUE": "kept-overlay-value",
    }
    assert environment["SKELETON_HOME_EDGE_01_HOSTNAME"] == "live-home-edge"
    assert environment["SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"] == "synthetic-hmac-marker"


def _enable_recovered_runtime_marker(monkeypatch) -> None:
    monkeypatch.setattr(child_env, "pinned_codex_recovery_marker_present", lambda _env: True)


def _enable_canonical_runner_context(monkeypatch) -> None:
    monkeypatch.setattr(child_env, "is_canonical_systemd_runner_context", lambda _env: True)


def test_codegen_environment_binds_wrapper_to_pinned_codex_only(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex-pinned"
    codex.write_text("", encoding="utf-8")
    authority = {"HOME": str(tmp_path), "PATH": str(bin_dir)}
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    _enable_canonical_runner_context(monkeypatch)
    _enable_recovered_runtime_marker(monkeypatch)
    monkeypatch.setattr(child_env, "pinned_codex_runtime_path", lambda _env: str(codex))
    sanitized = sanitize_codegen_child_environment(
        {
            **authority,
            "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET": "must-not-survive",
            "SKELETON_OPENHANDS_BIN": "/untrusted/openhands",
            "OPENROUTER_API_KEY": "must-not-survive",
        },
        authority_environment=authority,
    )
    wrapper_dir = tmp_path / ".local" / "state" / "skeleton-runner" / "codegen-fallback-bin"
    wrapper = wrapper_dir / "codex"
    assert wrapper.is_file()
    assert sanitized["HOME"] == str(tmp_path)
    assert sanitized["PATH"].split(":", 1)[0] == str(wrapper_dir)
    assert sanitized["SKELETON_REAL_CODEX_BIN"] == str(codex.resolve())
    assert "SKELETON_OPENHANDS_BIN" not in sanitized
    assert "OPENROUTER_API_KEY" not in sanitized
    assert "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET" not in sanitized


def test_validation_worktree_never_binds_live_recovery_wrapper(tmp_path: Path, monkeypatch) -> None:
    authority = {
        "HOME": str(tmp_path),
        "PATH": "/trusted/bin",
        "INVOCATION_ID": "live-systemd-marker",
    }
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    monkeypatch.setattr(child_env, "is_canonical_systemd_runner_context", lambda _env: False)
    monkeypatch.setattr(
        child_env,
        "pinned_codex_recovery_marker_present",
        lambda _env: (_ for _ in ()).throw(AssertionError("worktree must not inspect live marker")),
    )
    monkeypatch.setattr(
        child_env,
        "pinned_codex_runtime_path",
        lambda _env: (_ for _ in ()).throw(AssertionError("worktree must not probe live Codex")),
    )
    sanitized = sanitize_codegen_child_environment(
        {"HOME": "/overlay/home", "PATH": "/overlay/bin"},
        authority_environment=authority,
    )
    assert sanitized == {"HOME": "/overlay/home", "PATH": "/overlay/bin"}
    assert not (tmp_path / ".local" / "state" / "skeleton-runner").exists()


def test_caller_overlay_cannot_replace_recovery_home_or_path(tmp_path: Path, monkeypatch) -> None:
    trusted_home = tmp_path / "trusted-home"
    trusted_bin = tmp_path / "trusted-bin"
    overlay_home = tmp_path / "overlay-home"
    trusted_home.mkdir()
    trusted_bin.mkdir()
    overlay_home.mkdir()
    codex = trusted_bin / "codex-pinned"
    codex.write_text("", encoding="utf-8")
    authority = {"HOME": str(trusted_home), "PATH": str(trusted_bin), "INVOCATION_ID": "trusted"}
    observed: list[dict[str, str]] = []

    monkeypatch.setattr(
        child_env,
        "should_attempt_codex_runtime_recovery",
        lambda env: observed.append(dict(env)) or False,
    )
    _enable_canonical_runner_context(monkeypatch)
    _enable_recovered_runtime_marker(monkeypatch)
    monkeypatch.setattr(
        child_env,
        "pinned_codex_runtime_path",
        lambda env: observed.append(dict(env)) or str(codex),
    )

    sanitized = sanitize_codegen_child_environment(
        {
            "HOME": str(overlay_home),
            "PATH": "/overlay/bin",
            "INVOCATION_ID": "overlay",
            "SKELETON_REAL_CODEX_BIN": "/overlay/codex",
            "SKELETON_OPENHANDS_BIN": "/overlay/openhands",
        },
        authority_environment=authority,
    )

    assert observed
    assert all(item.get("HOME") == str(trusted_home) for item in observed)
    assert all(item.get("PATH") == str(trusted_bin) for item in observed)
    assert all(item.get("INVOCATION_ID") == "trusted" for item in observed)
    assert sanitized["HOME"] == str(trusted_home)
    assert sanitized["SKELETON_REAL_CODEX_BIN"] == str(codex.resolve())
    assert sanitized["PATH"].endswith(str(trusted_bin))
    assert "SKELETON_OPENHANDS_BIN" not in sanitized
    assert not (overlay_home / ".local" / "state" / "skeleton-runner").exists()


def test_codegen_wrapper_binds_pinned_codex_without_external_executor(tmp_path: Path, monkeypatch) -> None:
    codex = tmp_path / "codex-pinned"
    codex.write_text("", encoding="utf-8")
    authority = {"HOME": str(tmp_path), "PATH": "/trusted/bin"}
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    _enable_canonical_runner_context(monkeypatch)
    _enable_recovered_runtime_marker(monkeypatch)
    monkeypatch.setattr(child_env, "pinned_codex_runtime_path", lambda _env: str(codex))
    sanitized = sanitize_codegen_child_environment(
        {"HOME": "/overlay/home", "PATH": "/stale/bin"}, authority_environment=authority
    )
    wrapper = tmp_path / ".local" / "state" / "skeleton-runner" / "codegen-fallback-bin" / "codex"
    assert wrapper.is_file()
    assert sanitized["HOME"] == str(tmp_path)
    assert sanitized["SKELETON_REAL_CODEX_BIN"] == str(codex.resolve())
    assert "SKELETON_OPENHANDS_BIN" not in sanitized
    assert sanitized["PATH"] == f"{wrapper.parent}:/trusted/bin"


def test_wrapper_is_not_installed_before_recovery_marker(tmp_path: Path, monkeypatch) -> None:
    authority = {"HOME": str(tmp_path), "PATH": "/trusted/bin"}
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    _enable_canonical_runner_context(monkeypatch)
    monkeypatch.setattr(child_env, "pinned_codex_recovery_marker_present", lambda _env: False)
    monkeypatch.setattr(
        child_env,
        "pinned_codex_runtime_path",
        lambda _env: (_ for _ in ()).throw(AssertionError("must not probe before marker")),
    )
    sanitized = sanitize_codegen_child_environment(
        {"HOME": str(tmp_path), "PATH": "/stale/bin"}, authority_environment=authority
    )
    assert sanitized["PATH"] == "/stale/bin"
    assert "SKELETON_REAL_CODEX_BIN" not in sanitized


def test_wrapper_is_not_installed_when_marked_runtime_is_unverified(tmp_path: Path, monkeypatch) -> None:
    authority = {"HOME": str(tmp_path), "PATH": "/trusted/bin"}
    monkeypatch.setattr(child_env, "should_attempt_codex_runtime_recovery", lambda _env: False)
    _enable_canonical_runner_context(monkeypatch)
    _enable_recovered_runtime_marker(monkeypatch)
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
    _write_executable(
        openhands,
        "#!/bin/sh\nprintf '%s\\n' called > \"$OPENHANDS_MARKER\"\nexit 0\n",
    )
    _write_executable(wrapper, child_env._WRAPPER)
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": environment.get("PATH", "/usr/bin:/bin"),
            "SKELETON_REAL_CODEX_BIN": str(codex),
            "SKELETON_OPENHANDS_BIN": str(openhands),
            "SKELETON_CODEGEN_ORIGINAL_PATH": environment.get("PATH", "/usr/bin:/bin"),
            "OPENHANDS_MARKER": str(fallback_marker),
            "OPENROUTER_API_KEY": "synthetic-provider-secret",
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


def test_codegen_wrapper_does_not_fallback_for_exact_model_metadata_decoder_failure(tmp_path: Path) -> None:
    result, fallback_marker = _run_wrapper(
        tmp_path,
        codex_body="#!/bin/sh\nprintf '%s\\n' 'failed to decode models response: unknown variant `max`' >&2\nexit 1\n",
    )
    assert result.returncode == 1
    assert "unknown variant `max`" in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert "RESULT: OK" not in result.stdout
    assert not fallback_marker.exists()


def test_codegen_wrapper_provider_quota_fails_closed_without_openhands(tmp_path: Path) -> None:
    result, fallback_marker = _run_wrapper(
        tmp_path,
        codex_body="#!/bin/sh\nprintf '%s\\n' 'usage limit reached' >&2\nexit 1\n",
    )
    assert result.returncode == 1
    assert "usage limit reached" in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert "RESULT: OK" not in result.stdout
    assert not fallback_marker.exists()


def test_codegen_wrapper_does_not_fallback_for_unrelated_codex_failure(tmp_path: Path) -> None:
    result, fallback_marker = _run_wrapper(
        tmp_path,
        codex_body="#!/bin/sh\nprintf '%s\\n' 'unrelated synthetic codex failure' >&2\nexit 7\n",
    )
    assert result.returncode == 7
    assert "unrelated synthetic codex failure" in result.stderr
    assert "SKELETON_CODEGEN_PROVIDER=openhands" not in result.stdout
    assert "RESULT: OK" not in result.stdout
    assert not fallback_marker.exists()
