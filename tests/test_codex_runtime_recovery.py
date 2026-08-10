from __future__ import annotations

from pathlib import Path
import subprocess

import core.codex_runtime_recovery as recovery


def _completed(
    argv: list[str], returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def test_recovery_gate_requires_canonical_root_systemd_and_enable_marker(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "enabled"
    marker.write_text("enabled\n", encoding="utf-8")
    env = {"INVOCATION_ID": "unit"}

    assert recovery.should_attempt_codex_runtime_recovery(
        env,
        repository_root=tmp_path,
        canonical_root=tmp_path,
        enable_marker=marker,
    )
    assert not recovery.should_attempt_codex_runtime_recovery(
        {},
        repository_root=tmp_path,
        canonical_root=tmp_path,
        enable_marker=marker,
    )
    assert not recovery.should_attempt_codex_runtime_recovery(
        env,
        repository_root=tmp_path / "worktree",
        canonical_root=tmp_path,
        enable_marker=marker,
    )


def test_recovery_installs_exact_target_smokes_and_marks_success(
    tmp_path: Path, monkeypatch
) -> None:
    env = {"HOME": str(tmp_path), "PATH": "/fake/bin"}
    installed = {"version": "0.125.0"}
    installs: list[str] = []

    monkeypatch.setattr(
        recovery.shutil,
        "which",
        lambda name, path=None: f"/fake/bin/{name}",
    )
    monkeypatch.setattr(
        recovery,
        "_state_paths",
        lambda environment: (tmp_path / "ok", tmp_path / "lock"),
    )

    def fake_run(argv, environment, *, timeout, cwd=None):
        if argv[:3] == ["/fake/bin/npm", "prefix", "-g"]:
            return _completed(argv, 0, "/fake\n")
        if argv == ["/fake/bin/codex", "--version"]:
            return _completed(argv, 0, f"codex-cli {installed['version']}\n")
        if argv[:3] == ["/fake/bin/npm", "install", "-g"]:
            version = argv[3].rsplit("@", 1)[1]
            installs.append(version)
            installed["version"] = version
            return _completed(argv, 0)
        if argv[:2] == ["/fake/bin/codex", "exec"]:
            return _completed(argv, 0, "RESULT: OK\n")
        raise AssertionError(argv)

    monkeypatch.setattr(recovery, "_run", fake_run)

    assert recovery.ensure_pinned_codex_runtime(env)
    assert installs == [recovery.TARGET_CODEX_VERSION]
    assert (tmp_path / "ok").read_text(encoding="utf-8") == "version=0.145.0\n"


def test_recovery_keeps_exact_target_when_smoke_reaches_provider_quota(
    tmp_path: Path, monkeypatch
) -> None:
    env = {"HOME": str(tmp_path), "PATH": "/fake/bin"}
    installed = {"version": "0.125.0"}
    installs: list[str] = []

    monkeypatch.setattr(
        recovery.shutil,
        "which",
        lambda name, path=None: f"/fake/bin/{name}",
    )
    monkeypatch.setattr(
        recovery,
        "_state_paths",
        lambda environment: (tmp_path / "ok", tmp_path / "lock"),
    )

    def fake_run(argv, environment, *, timeout, cwd=None):
        if argv[:3] == ["/fake/bin/npm", "prefix", "-g"]:
            return _completed(argv, 0, "/fake\n")
        if argv == ["/fake/bin/codex", "--version"]:
            return _completed(argv, 0, f"codex-cli {installed['version']}\n")
        if argv[:3] == ["/fake/bin/npm", "install", "-g"]:
            version = argv[3].rsplit("@", 1)[1]
            installs.append(version)
            installed["version"] = version
            return _completed(argv, 0)
        if argv[:2] == ["/fake/bin/codex", "exec"]:
            return _completed(argv, 1, stderr="usage limit reached")
        raise AssertionError(argv)

    monkeypatch.setattr(recovery, "_run", fake_run)

    assert recovery.ensure_pinned_codex_runtime(env)
    assert installs == [recovery.TARGET_CODEX_VERSION]
    assert installed["version"] == recovery.TARGET_CODEX_VERSION
    assert (tmp_path / "ok").read_text(encoding="utf-8") == "version=0.145.0\n"


def test_recovery_rolls_back_exact_prior_version_when_smoke_fails(
    tmp_path: Path, monkeypatch
) -> None:
    env = {"HOME": str(tmp_path), "PATH": "/fake/bin"}
    installed = {"version": "0.125.0"}
    installs: list[str] = []

    monkeypatch.setattr(
        recovery.shutil,
        "which",
        lambda name, path=None: f"/fake/bin/{name}",
    )
    monkeypatch.setattr(
        recovery,
        "_state_paths",
        lambda environment: (tmp_path / "ok", tmp_path / "lock"),
    )

    def fake_run(argv, environment, *, timeout, cwd=None):
        if argv[:3] == ["/fake/bin/npm", "prefix", "-g"]:
            return _completed(argv, 0, "/fake\n")
        if argv == ["/fake/bin/codex", "--version"]:
            return _completed(argv, 0, f"codex-cli {installed['version']}\n")
        if argv[:3] == ["/fake/bin/npm", "install", "-g"]:
            version = argv[3].rsplit("@", 1)[1]
            installs.append(version)
            installed["version"] = version
            return _completed(argv, 0)
        if argv[:2] == ["/fake/bin/codex", "exec"]:
            return _completed(
                argv,
                1,
                stderr="failed to decode models response: unknown variant `max`",
            )
        raise AssertionError(argv)

    monkeypatch.setattr(recovery, "_run", fake_run)

    assert not recovery.ensure_pinned_codex_runtime(env)
    assert installs == [recovery.TARGET_CODEX_VERSION, "0.125.0"]
    assert installed["version"] == "0.125.0"
    assert not (tmp_path / "ok").exists()
