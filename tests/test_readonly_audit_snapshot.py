from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import readonly_audit_snapshot as audit


def init_repo(path: Path) -> str:
    subprocess.run(("git", "init"), cwd=path, check=True, stdout=subprocess.PIPE)
    subprocess.run(("git", "config", "user.email", "audit@example.invalid"), cwd=path, check=True)
    subprocess.run(("git", "config", "user.name", "Audit Test"), cwd=path, check=True)
    (path / "pyproject.toml").write_text(
        """
[project]
name = "sample"
version = "0.0.0"
[project.optional-dependencies]
dev = ["pytest"]
aufmass-geometry = ["ezdxf"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (path / "core").mkdir()
    (path / "core" / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    subprocess.run(("git", "add", "."), cwd=path, check=True)
    subprocess.run(("git", "commit", "-m", "init"), cwd=path, check=True, stdout=subprocess.PIPE)
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def fake_runner(command_log: list[tuple[tuple[str, ...], Path]]) -> audit.Runner:
    def run(command, cwd: Path) -> audit.CommandResult:
        command_tuple = tuple(command)
        command_log.append((command_tuple, cwd))
        if command_tuple[:3] == (os.sys.executable, "-m", "venv"):
            venv = Path(command_tuple[3])
            python_bin = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python_bin.parent.mkdir(parents=True, exist_ok=True)
            python_bin.write_text("#!/usr/bin/env python\n", encoding="utf-8")
            return audit.CommandResult(command_tuple, 0)
        return audit.CommandResult(command_tuple, 0, stdout="3 passed in 0.01s\n")

    return run


def test_denylisted_paths_and_unsafe_symlinks_are_excluded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo(repo)
    (repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (repo / "secrets").mkdir()
    (repo / "secrets" / "key.txt").write_text("secret\n", encoding="utf-8")
    (repo / "runtime").mkdir()
    (repo / "runtime" / "state.db").write_text("db\n", encoding="utf-8")
    (repo / "outside.txt").write_text("outside\n", encoding="utf-8")
    (repo / "escape").symlink_to(repo / "outside.txt")
    subprocess.run(("git", "add", ".env", "secrets/key.txt", "runtime/state.db", "escape"), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-m", "excluded paths"), cwd=repo, check=True, stdout=subprocess.PIPE)
    sha = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()

    manifest = audit.create_snapshot(repo, tmp_path / "snapshot", "owner/repo", sha)

    excluded = {item["path"]: item["reason"] for item in manifest["exclusions"]}
    assert excluded[".env"] == "env_file"
    assert excluded["secrets/key.txt"] == "excluded_directory"
    assert excluded["runtime/state.db"] == "excluded_directory"
    assert excluded["escape"] in {"unsafe_symlink", "symlink_excluded"}
    assert not (tmp_path / "snapshot" / ".env").exists()
    assert not (tmp_path / "snapshot" / "secrets" / "key.txt").exists()


def test_snapshot_hashes_are_deterministic(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo(repo)

    first = audit.create_snapshot(repo, tmp_path / "snapshot-one", "owner/repo", sha)
    second = audit.create_snapshot(repo, tmp_path / "snapshot-two", "owner/repo", sha)

    assert first["files"] == second["files"]


def test_source_snapshot_is_not_writable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo(repo)
    snapshot = tmp_path / "snapshot"

    audit.create_snapshot(repo, snapshot, "owner/repo", sha)

    assert not audit.is_writable_by_mode(snapshot)
    assert not audit.is_writable_by_mode(snapshot / "core" / "sample.py")


def test_validation_writes_only_to_disposable_copy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo(repo)
    snapshot = tmp_path / "snapshot"
    audit.create_snapshot(repo, snapshot, "owner/repo", sha)
    command_log: list[tuple[tuple[str, ...], Path]] = []

    audit.run_validation(snapshot, "owner/repo", sha, tmp_path / "evidence", runner=fake_runner(command_log), profiles=("core",))

    assert command_log
    assert all(snapshot not in [cwd, *cwd.parents] for _, cwd in command_log)
    assert all(not cwd.exists() for _, cwd in command_log)


def test_arbitrary_profile_or_command_input_is_impossible(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo(repo)
    snapshot = tmp_path / "snapshot"
    audit.create_snapshot(repo, snapshot, "owner/repo", sha)

    with pytest.raises(audit.AuditValidationError, match="unsupported validation profile"):
        audit.run_validation(snapshot, "owner/repo", sha, tmp_path / "evidence", profiles=("shell",))
    with pytest.raises(SystemExit):
        audit.parse_args(["--profile", "shell"])


def test_cleanup_happens_after_success_and_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo(repo)
    snapshot = tmp_path / "snapshot"
    audit.create_snapshot(repo, snapshot, "owner/repo", sha)

    audit.run_validation(snapshot, "owner/repo", sha, tmp_path / "success", runner=fake_runner([]), profiles=("core",))

    def failing_runner(command, cwd: Path) -> audit.CommandResult:
        if tuple(command)[:3] == (os.sys.executable, "-m", "venv"):
            return fake_runner([])(command, cwd)
        return audit.CommandResult(tuple(command), 1, stderr="failed\n")

    with pytest.raises(audit.AuditValidationError):
        audit.run_validation(snapshot, "owner/repo", sha, tmp_path / "failure", runner=failing_runner, profiles=("core",))


def test_cleanup_failure_blocks_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo(repo)
    snapshot = tmp_path / "snapshot"
    audit.create_snapshot(repo, snapshot, "owner/repo", sha)
    monkeypatch.setattr(audit, "cleanup_path", lambda path: False)

    with pytest.raises(audit.AuditValidationError):
        audit.run_validation(snapshot, "owner/repo", sha, tmp_path / "evidence", runner=fake_runner([]), profiles=("core",))

    summary = json.loads((tmp_path / "evidence" / audit.SUMMARY_NAME).read_text(encoding="utf-8"))
    assert summary["cleanup_status"] == "failed"
    assert summary["success"] is False


def test_public_evidence_contains_no_absolute_path_token_or_environment_assignment() -> None:
    dirty = "/home/runner/work/repo TOKEN=ghp_abcdefghijklmnopqrstuvwxyz123456 SECRET=value\n"
    clean = audit.sanitize_public_text(dirty)

    assert "/home/runner" not in clean
    assert "ghp_" not in clean
    assert "SECRET=value" not in clean


def test_geometry_profile_rejects_skipped_geometry_tests() -> None:
    with pytest.raises(audit.AuditValidationError, match="skipped geometry"):
        audit.assert_no_skipped_geometry_tests("1 passed, 1 skipped in 0.01s")


def test_manifest_records_repository_sha_file_count_python_and_platform(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo(repo)

    manifest = audit.create_snapshot(repo, tmp_path / "snapshot", "owner/repo", sha)

    assert manifest["repository"] == "owner/repo"
    assert manifest["sha"] == sha
    assert manifest["file_count"] == len(manifest["files"])
    assert manifest["python_version"]
    assert manifest["platform"]
