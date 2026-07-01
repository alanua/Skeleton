from __future__ import annotations

import argparse
import compileall
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "audit_manifest.json"
SUMMARY_NAME = "validation_summary.json"
ALLOWED_PROFILES = ("core", "aufmass_geometry")

EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "private",
    "runtime",
    "secrets",
    "site-packages",
}
EXCLUDED_FILE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pyc",
    ".pyo",
}
TOKEN_RE = re.compile(
    r"(?i)(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|[A-Za-z0-9_=-]{32,})"
)
ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])/(home|tmp|var|etc|opt|runner|workspace)/[^\s\"']+")
ENV_ASSIGNMENT_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*=[^\s]+")


class AuditValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str], Path], CommandResult]


@dataclass(frozen=True)
class GitTreeEntry:
    mode: str
    object_type: str
    object_id: str
    path: Path


def run_subprocess(command: Sequence[str], cwd: Path) -> CommandResult:
    completed = subprocess.run(
        tuple(command),
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=900,
    )
    return CommandResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def verified_head_sha(repo_root: Path, expected_sha: str | None = None) -> str:
    result = run_subprocess(("git", "rev-parse", "HEAD"), repo_root)
    if result.returncode != 0:
        raise AuditValidationError("could not resolve checked-out SHA")
    sha = result.stdout.strip()
    if expected_sha and sha != expected_sha:
        raise AuditValidationError("checked-out SHA does not match expected event SHA")
    return sha


def tracked_entries(repo_root: Path, sha: str) -> list[GitTreeEntry]:
    result = subprocess.run(
        ("git", "ls-tree", "-rz", "--full-tree", sha),
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise AuditValidationError("could not list tracked files")
    entries = []
    for raw in result.stdout.split(b"\0"):
        if raw:
            metadata, path = raw.decode("utf-8").split("\t", 1)
            mode, object_type, object_id = metadata.split(" ", 2)
            entries.append(GitTreeEntry(mode, object_type, object_id, Path(path)))
    return sorted(entries, key=lambda entry: entry.path.as_posix())


def blob_bytes(repo_root: Path, entry: GitTreeEntry) -> bytes:
    result = subprocess.run(
        ("git", "cat-file", "-p", entry.object_id),
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise AuditValidationError("could not read tracked file content")
    return result.stdout


def exclusion_reason(relative_path: Path, source_path: Path, repo_root: Path, mode: str | None = None) -> str | None:
    parts = relative_path.parts
    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return "excluded_directory"
    if relative_path.name == ".env" or relative_path.name.startswith(".env."):
        return "env_file"
    if relative_path.suffix in EXCLUDED_FILE_SUFFIXES:
        return "local_database_or_cache"
    if mode == "120000" or source_path.is_symlink():
        try:
            resolved = source_path.resolve(strict=False)
        except OSError:
            return "unsafe_symlink"
        try:
            resolved.relative_to(repo_root.resolve())
        except ValueError:
            return "unsafe_symlink"
        return "symlink_excluded"
    return None


def make_read_only(path: Path) -> None:
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        mode = child.lstat().st_mode
        if child.is_symlink():
            continue
        child.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def is_writable_by_mode(path: Path) -> bool:
    return bool(path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def create_snapshot(repo_root: Path, destination: Path, repository: str, sha: str) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=False)
    exclusions: list[dict[str, str]] = []
    hashes: list[dict[str, str]] = []

    for entry in tracked_entries(repo_root, sha):
        relative_path = entry.path
        source_path = repo_root / relative_path
        reason = exclusion_reason(relative_path, source_path, repo_root, entry.mode)
        if reason:
            exclusions.append({"path": relative_path.as_posix(), "reason": reason})
            continue
        if entry.object_type != "blob" or entry.mode == "120000":
            exclusions.append({"path": relative_path.as_posix(), "reason": "not_regular_file"})
            continue

        target_path = destination / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        content = blob_bytes(repo_root, entry)
        target_path.write_bytes(content)
        digest = sha256(content).hexdigest()
        hashes.append({"path": relative_path.as_posix(), "sha256": digest})

    manifest = {
        "schema": "skeleton.readonly_audit_manifest.v1",
        "repository": repository,
        "sha": sha,
        "file_count": len(hashes),
        "files": hashes,
        "exclusions": exclusions,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }
    (destination / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    make_read_only(destination)
    return manifest


def load_optional_groups(snapshot: Path) -> set[str]:
    pyproject = snapshot / "pyproject.toml"
    if not pyproject.is_file():
        return set()
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    groups = data.get("project", {}).get("optional-dependencies", {})
    if not isinstance(groups, dict):
        return set()
    return {key for key in groups if isinstance(key, str)}


def copy_disposable_snapshot(snapshot: Path, destination: Path) -> None:
    ignore = shutil.ignore_patterns(MANIFEST_NAME, ".git", ".venv", "__pycache__")
    shutil.copytree(snapshot, destination, symlinks=False, ignore=ignore, dirs_exist_ok=True)
    for child in destination.rglob("*"):
        if child.is_symlink():
            child.unlink()
        elif child.exists():
            child.chmod(child.stat().st_mode | stat.S_IWUSR)
    destination.chmod(destination.stat().st_mode | stat.S_IWUSR)


def fixed_commands_for(profile: str, python_bin: Path) -> list[tuple[str, ...]]:
    if profile == "core":
        return [
            (str(python_bin), "-m", "pip", "install", "-e", ".[dev]"),
            (str(python_bin), "-m", "pytest", "-q"),
            ("git", "init"),
            ("git", "add", "."),
            ("git", "diff", "--cached", "--check"),
        ]
    if profile == "aufmass_geometry":
        return [
            (str(python_bin), "-m", "pip", "install", "-e", ".[dev,aufmass-geometry]"),
            (
                str(python_bin),
                "-m",
                "pytest",
                "-q",
                "tests/test_aufmass_geometry_core2d.py",
                "tests/test_aufmass_dxf_adapter.py",
            ),
            (str(python_bin), "scripts/aufmass_geometry_healthcheck.py"),
            (str(python_bin), "scripts/aufmass_geometry_benchmark.py", "--synthetic"),
        ]
    raise AuditValidationError("unsupported validation profile")


def duration_class(seconds: float) -> str:
    if seconds < 30:
        return "short"
    if seconds < 300:
        return "medium"
    return "long"


def sanitize_public_text(text: str) -> str:
    text = TOKEN_RE.sub("[redacted-token]", text)
    text = ABSOLUTE_PATH_RE.sub("[redacted-path]", text)
    text = ENV_ASSIGNMENT_RE.sub("[redacted-env-assignment]", text)
    return text


def public_command(command: Sequence[str]) -> str:
    parts = []
    for item in command:
        if item.startswith("/") or item.startswith(str(Path.home())):
            parts.append("[path]")
        else:
            parts.append(item)
    return sanitize_public_text(" ".join(parts))


def pytest_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for key in counts:
        matches = re.findall(rf"(\d+)\s+{key}", output)
        if matches:
            counts[key] = int(matches[-1])
    return counts


def assert_no_skipped_geometry_tests(output: str) -> None:
    counts = pytest_counts(output)
    if counts["skipped"]:
        raise AuditValidationError("geometry profile rejected skipped geometry/DXF tests")


def compile_project_python_files(workdir: Path) -> CommandResult:
    paths = [workdir / name for name in ("core", "scripts", "tools", "tests") if (workdir / name).exists()]
    ok = compileall.compile_dir(str(workdir), quiet=1, force=True) if not paths else all(
        compileall.compile_dir(str(path), quiet=1, force=True) for path in paths
    )
    return CommandResult(("python", "-m", "compileall", "project-python-files"), 0 if ok else 1)


def create_venv(workdir: Path, runner: Runner) -> Path:
    venv = workdir / ".venv"
    result = runner((sys.executable, "-m", "venv", str(venv)), workdir)
    if result.returncode != 0:
        raise AuditValidationError("virtual environment creation failed")
    python_bin = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return python_bin


def run_profile(profile: str, workdir: Path, runner: Runner) -> dict[str, object]:
    if profile not in ALLOWED_PROFILES:
        raise AuditValidationError("unsupported validation profile")

    started = time.monotonic()
    command_results = [compile_project_python_files(workdir)]
    python_bin = create_venv(workdir, runner)
    command_results.extend(runner(command, workdir) for command in fixed_commands_for(profile, python_bin))

    combined_output = "\n".join(result.stdout + "\n" + result.stderr for result in command_results)
    if profile == "aufmass_geometry":
        assert_no_skipped_geometry_tests(combined_output)
    success = all(result.returncode == 0 for result in command_results)
    counts = pytest_counts(combined_output)
    return {
        "profile": profile,
        "success": success,
        "pass_count": counts["passed"],
        "fail_count": counts["failed"] + sum(1 for result in command_results if result.returncode != 0),
        "skip_count": counts["skipped"],
        "duration_class": duration_class(time.monotonic() - started),
        "commands": [public_command(result.command) for result in command_results],
        "public_log_excerpt": sanitize_public_text(combined_output)[-4000:],
    }


def cleanup_path(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return not path.exists()


def run_validation(
    snapshot: Path,
    repository: str,
    sha: str,
    output_dir: Path,
    *,
    runner: Runner = run_subprocess,
    profiles: Sequence[str] = ALLOWED_PROFILES,
) -> dict[str, object]:
    if any(profile not in ALLOWED_PROFILES for profile in profiles):
        raise AuditValidationError("unsupported validation profile")
    if is_writable_by_mode(snapshot):
        raise AuditValidationError("source snapshot is writable")

    output_dir.mkdir(parents=True, exist_ok=True)
    optional_groups = load_optional_groups(snapshot)
    profile_summaries: list[dict[str, object]] = []
    cleanup_ok = True

    for profile in profiles:
        if profile == "aufmass_geometry" and "aufmass-geometry" not in optional_groups:
            profile_summaries.append(
                {
                    "profile": profile,
                    "success": True,
                    "skipped": True,
                    "reason": "optional group is not declared",
                    "pass_count": 0,
                    "fail_count": 0,
                    "skip_count": 1,
                    "duration_class": "short",
                    "public_log_excerpt": "",
                }
            )
            continue

        disposable = Path(tempfile.mkdtemp(prefix=f"readonly-audit-{profile}-"))
        try:
            copy_disposable_snapshot(snapshot, disposable)
            profile_summaries.append(run_profile(profile, disposable, runner))
        except Exception as exc:
            profile_summaries.append(
                {
                    "profile": profile,
                    "success": False,
                    "pass_count": 0,
                    "fail_count": 1,
                    "skip_count": 0,
                    "duration_class": "short",
                    "public_log_excerpt": sanitize_public_text(str(exc)),
                }
            )
        finally:
            cleanup_ok = cleanup_path(disposable) and cleanup_ok

    success = cleanup_ok and all(bool(item.get("success")) for item in profile_summaries)
    summary = {
        "schema": "skeleton.readonly_audit_validation_summary.v1",
        "repository": repository,
        "sha": sha,
        "success": success,
        "cleanup_status": "ok" if cleanup_ok else "failed",
        "success_criteria": [
            "snapshot derived from tracked files at verified SHA",
            "source snapshot remains read-only",
            "validation runs only in disposable writable copies",
            "cleanup succeeds after every profile",
            "public evidence is sanitized and bounded",
        ],
        "profiles": profile_summaries,
    }
    (output_dir / SUMMARY_NAME).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not success:
        raise AuditValidationError("read-only audit validation failed")
    return summary


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create and validate a read-only audit snapshot.")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "alanua/Skeleton"))
    parser.add_argument("--expected-sha", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "audit_evidence")
    parser.add_argument("--profile", choices=("all", *ALLOWED_PROFILES), default="all")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    sha = verified_head_sha(ROOT, args.expected_sha)
    output_dir = args.output_dir
    snapshot = output_dir / "source_snapshot"
    if snapshot.exists():
        shutil.rmtree(snapshot)
    manifest = create_snapshot(ROOT, snapshot, args.repository, sha)
    shutil.copyfile(snapshot / MANIFEST_NAME, output_dir / MANIFEST_NAME)
    profiles = ALLOWED_PROFILES if args.profile == "all" else (args.profile,)
    run_validation(snapshot, args.repository, str(manifest["sha"]), output_dir, profiles=profiles)
    print(f"readonly audit validation complete for {args.repository}@{sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
