from __future__ import annotations

from pathlib import Path
import subprocess

import core.codex_runtime_recovery as recovery


def _completed(argv: list[str], returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def test_recovery_gate_requires_canonical_root_systemd_and_enable_marker(tmp_path: Path) -> None:
    marker = tmp_path / "enabled"
    marker.write_text("enabled\n", encoding="utf-8")
    env = {"INVOCATION_ID": "unit"}
    assert recovery.is_canonical_systemd_runner_context(
        env, repository_root=tmp_path, canonical_root=tmp_path
    )
    assert not recovery.is_canonical_systemd_runner_context(
        {}, repository_root=tmp_path, canonical_root=tmp_path
    )
    assert not recovery.is_canonical_systemd_runner_context(
        env, repository_root=tmp_path / "worktree", canonical_root=tmp_path
    )
    assert recovery.should_attempt_codex_runtime_recovery(env, repository_root=tmp_path, canonical_root=tmp_path, enable_marker=marker)
    assert not recovery.should_attempt_codex_runtime_recovery({}, repository_root=tmp_path, canonical_root=tmp_path, enable_marker=marker)
    assert not recovery.should_attempt_codex_runtime_recovery(env, repository_root=tmp_path / "worktree", canonical_root=tmp_path, enable_marker=marker)


def test_runtime_path_uses_npm_prefix_and_ignores_stale_codex_path(monkeypatch) -> None:
    which_calls: list[str] = []

    def fake_which(name: str, path=None):
        which_calls.append(name)
        if name == "npm":
            return "/trusted/bin/npm"
        if name == "codex":
            return "/stale/service/bin/codex"
        return None

    monkeypatch.setattr(recovery.shutil, "which", fake_which)
    monkeypatch.setattr(recovery, "_safe_run", lambda argv, environment, *, timeout, cwd=None: _completed(argv, 0, "/canonical/npm\n"))
    npm_path, codex_path = recovery._global_runtime_paths({"PATH": "/stale/service/bin"})
    assert npm_path == "/trusted/bin/npm"
    assert codex_path == str(Path("/canonical/npm/bin/codex").resolve(strict=False))
    assert which_calls == ["npm"]


def _install_fake_runtime(tmp_path: Path, monkeypatch, *, smoke_stderr: str = "", install_ok: bool = True):
    env = {"HOME": str(tmp_path), "PATH": "/fake/bin"}
    installed = {"version": "0.125.0"}
    installs: list[str] = []
    monkeypatch.setattr(recovery.shutil, "which", lambda name, path=None: f"/fake/bin/{name}")
    monkeypatch.setattr(recovery, "_state_paths", lambda environment: (tmp_path / "ok", tmp_path / "lock"))

    def fake_run(argv, environment, *, timeout, cwd=None):
        if argv[:3] == ["/fake/bin/npm", "prefix", "-g"]:
            return _completed(argv, 0, "/fake\n")
        if argv == ["/fake/bin/codex", "--version"]:
            return _completed(argv, 0, f"codex-cli {installed['version']}\n")
        if argv[:3] == ["/fake/bin/npm", "install", "-g"]:
            version = argv[3].rsplit("@", 1)[1]
            installs.append(version)
            if not install_ok and version == recovery.TARGET_CODEX_VERSION:
                return _completed(argv, 1, stderr="synthetic install failure")
            installed["version"] = version
            return _completed(argv, 0)
        if argv[:2] == ["/fake/bin/codex", "exec"]:
            assert "--skip-git-repo-check" in argv
            assert argv.index("--skip-git-repo-check") > argv.index("exec")
            if smoke_stderr:
                return _completed(argv, 1, stderr=smoke_stderr)
            return _completed(argv, 0, "RESULT: OK\n")
        raise AssertionError(argv)

    monkeypatch.setattr(recovery, "_run", fake_run)
    return env, installed, installs


def test_recovery_installs_exact_target_smokes_and_marks_success(tmp_path: Path, monkeypatch) -> None:
    env, _installed, installs = _install_fake_runtime(tmp_path, monkeypatch)
    result = recovery.recover_pinned_codex_runtime(env)
    assert result == recovery.CodexRuntimeRecoveryResult(True, "ready")
    assert recovery.ensure_pinned_codex_runtime(env)
    assert installs == [recovery.TARGET_CODEX_VERSION]
    assert (tmp_path / "ok").read_text(encoding="utf-8") == "version=0.145.0\n"


def test_recovery_keeps_exact_target_when_smoke_reaches_provider_quota(tmp_path: Path, monkeypatch) -> None:
    env, installed, installs = _install_fake_runtime(tmp_path, monkeypatch, smoke_stderr="usage limit reached")
    result = recovery.recover_pinned_codex_runtime(env)
    assert result == recovery.CodexRuntimeRecoveryResult(True, "ready_provider_unavailable")
    assert installs == [recovery.TARGET_CODEX_VERSION]
    assert installed["version"] == recovery.TARGET_CODEX_VERSION
    assert (tmp_path / "ok").read_text(encoding="utf-8") == "version=0.145.0\n"


def test_recovery_reports_metadata_failure_and_rolls_back(tmp_path: Path, monkeypatch) -> None:
    env, installed, installs = _install_fake_runtime(
        tmp_path,
        monkeypatch,
        smoke_stderr="failed to decode models response: unknown variant `max`",
    )
    result = recovery.recover_pinned_codex_runtime(env)
    assert result == recovery.CodexRuntimeRecoveryResult(False, "smoke_metadata_incompatible")
    assert installs == [recovery.TARGET_CODEX_VERSION, "0.125.0"]
    assert installed["version"] == "0.125.0"
    assert not (tmp_path / "ok").exists()


def test_recovery_reports_target_install_failure_without_child_output(tmp_path: Path, monkeypatch) -> None:
    env, installed, installs = _install_fake_runtime(tmp_path, monkeypatch, install_ok=False)
    result = recovery.recover_pinned_codex_runtime(env)
    assert result == recovery.CodexRuntimeRecoveryResult(False, "target_install_failed")
    assert installs == [recovery.TARGET_CODEX_VERSION, "0.125.0"]
    assert installed["version"] == "0.125.0"


def test_recovery_reports_missing_npm_without_path_disclosure(monkeypatch) -> None:
    monkeypatch.setattr(recovery.shutil, "which", lambda name, path=None: None)
    result = recovery.recover_pinned_codex_runtime({"HOME": "/home/agent", "PATH": "/synthetic"})
    assert result == recovery.CodexRuntimeRecoveryResult(False, "npm_runtime_binary_missing")
    assert "/synthetic" not in result.reason


def test_recovery_reports_existing_canonical_codex_unavailable(tmp_path: Path, monkeypatch) -> None:
    env = {"HOME": str(tmp_path), "PATH": "/fake/bin"}
    monkeypatch.setattr(recovery.shutil, "which", lambda name, path=None: "/fake/bin/npm" if name == "npm" else None)

    def fake_run(argv, environment, *, timeout, cwd=None):
        if argv[:3] == ["/fake/bin/npm", "prefix", "-g"]:
            return _completed(argv, 0, "/fake\n")
        if argv == ["/fake/bin/codex", "--version"]:
            return _completed(argv, 127)
        raise AssertionError(argv)

    monkeypatch.setattr(recovery, "_run", fake_run)
    result = recovery.recover_pinned_codex_runtime(env)
    assert result == recovery.CodexRuntimeRecoveryResult(False, "existing_codex_version_unavailable")


def test_compatibility_wrapper_returns_detailed_success_boolean(tmp_path: Path, monkeypatch) -> None:
    env, _installed, _installs = _install_fake_runtime(tmp_path, monkeypatch)
    assert recovery.ensure_pinned_codex_runtime(env) is True
