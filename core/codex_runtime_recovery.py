from __future__ import annotations

from collections.abc import Mapping
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


TARGET_CODEX_VERSION = "0.145.0"
TARGET_CODEX_PACKAGE = f"@openai/codex@{TARGET_CODEX_VERSION}"
TARGET_CODEX_MODEL = "gpt-5.5"
CANONICAL_RUNNER_ROOT = Path("/home/agent/agent-dev/repos/Skeleton")
RECOVERY_ENABLE_MARKER = Path("scripts/codex-runtime-recovery-0.144.4.enable")
SYSTEMD_INVOCATION_ENV = "INVOCATION_ID"
_VERSION_RE = re.compile(r"^codex-cli ([0-9]+\.[0-9]+\.[0-9]+)$")
_INSTALL_TIMEOUT_SECONDS = 240
_SMOKE_TIMEOUT_SECONDS = 120
_VERSION_TIMEOUT_SECONDS = 15
_PROVIDER_OUTAGE_MARKERS = (
    "usage limit",
    "rate limit",
    "quota",
    "insufficient_quota",
    "provider unavailable",
    "temporarily unavailable",
    "service unavailable",
    "try again at",
)
_SMOKE_PASS = "pass"
_SMOKE_PROVIDER_UNAVAILABLE = "provider_unavailable"
_SMOKE_FAILED = "failed"


class CodexRuntimeRecoveryError(RuntimeError):
    """Raised when the bounded Codex runtime recovery cannot complete safely."""


def should_attempt_codex_runtime_recovery(
    environment: Mapping[str, str],
    *,
    repository_root: Path | None = None,
    canonical_root: Path = CANONICAL_RUNNER_ROOT,
    enable_marker: Path | None = None,
) -> bool:
    """Limit the bootstrap to the explicitly enabled canonical systemd Runner."""
    root = (repository_root or Path(__file__).resolve().parents[1]).resolve(strict=False)
    canonical = canonical_root.resolve(strict=False)
    marker = enable_marker or (root / RECOVERY_ENABLE_MARKER)
    return (
        root == canonical
        and marker.is_file()
        and bool(environment.get(SYSTEMD_INVOCATION_ENV, "").strip())
    )


def _run(
    argv: list[str],
    environment: Mapping[str, str],
    *,
    timeout: int,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=dict(environment),
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _safe_run(
    argv: list[str],
    environment: Mapping[str, str],
    *,
    timeout: int,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return _run(argv, environment, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None


def _codex_version(codex_path: str, environment: Mapping[str, str]) -> str:
    result = _safe_run(
        [codex_path, "--version"],
        environment,
        timeout=_VERSION_TIMEOUT_SECONDS,
    )
    if result is None or result.returncode != 0:
        raise CodexRuntimeRecoveryError("codex_version_unavailable")
    match = _VERSION_RE.fullmatch(result.stdout.strip())
    if match is None:
        raise CodexRuntimeRecoveryError("codex_version_unparseable")
    return match.group(1)


def _global_runtime_paths(environment: Mapping[str, str]) -> tuple[str, str]:
    npm_path = shutil.which("npm", path=environment.get("PATH"))
    if not npm_path:
        raise CodexRuntimeRecoveryError("npm_runtime_binary_missing")

    prefix_result = _safe_run(
        [npm_path, "prefix", "-g"],
        environment,
        timeout=_VERSION_TIMEOUT_SECONDS,
    )
    if (
        prefix_result is None
        or prefix_result.returncode != 0
        or not prefix_result.stdout.strip()
    ):
        raise CodexRuntimeRecoveryError("npm_global_prefix_unavailable")

    prefix = Path(prefix_result.stdout.strip()).expanduser().resolve(strict=False)
    expected_codex = (prefix / "bin" / "codex").resolve(strict=False)
    return npm_path, str(expected_codex)


def pinned_codex_runtime_path(environment: Mapping[str, str]) -> str:
    """Return the exact npm-global Codex path only when the pinned version is active."""
    _npm_path, codex_path = _global_runtime_paths(environment)
    if _codex_version(codex_path, environment) != TARGET_CODEX_VERSION:
        raise CodexRuntimeRecoveryError("codex_runtime_version_mismatch")
    return codex_path


def _install_version(
    npm_path: str,
    version: str,
    environment: Mapping[str, str],
) -> bool:
    result = _safe_run(
        [
            npm_path,
            "install",
            "-g",
            f"@openai/codex@{version}",
            "--no-audit",
            "--no-fund",
        ],
        environment,
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )
    return result is not None and result.returncode == 0


def _smoke_codex(codex_path: str, environment: Mapping[str, str]) -> str:
    with tempfile.TemporaryDirectory(prefix="skeleton-codex-recovery-") as temp_dir:
        result = _safe_run(
            [
                codex_path,
                "exec",
                "--sandbox",
                "read-only",
                "--model",
                TARGET_CODEX_MODEL,
                "--cd",
                temp_dir,
                "Return exactly RESULT: OK. Do not modify files.",
            ],
            environment,
            timeout=_SMOKE_TIMEOUT_SECONDS,
            cwd=temp_dir,
        )
        if result is None:
            return _SMOKE_FAILED
        if result.returncode == 0 and "RESULT: OK" in {
            line.strip() for line in result.stdout.splitlines()
        }:
            return _SMOKE_PASS
        combined = f"{result.stdout}\n{result.stderr}".lower()
        if any(marker in combined for marker in _PROVIDER_OUTAGE_MARKERS):
            return _SMOKE_PROVIDER_UNAVAILABLE
        return _SMOKE_FAILED


def _state_paths(environment: Mapping[str, str]) -> tuple[Path, Path]:
    home_text = environment.get("HOME", "").strip()
    if not home_text:
        raise CodexRuntimeRecoveryError("runner_home_missing")
    home = Path(home_text).expanduser()
    if not home.is_absolute():
        raise CodexRuntimeRecoveryError("runner_home_not_absolute")
    state_dir = home / ".local" / "state" / "skeleton"
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = state_dir / f"codex-runtime-recovery-{TARGET_CODEX_VERSION}.ok"
    lock = state_dir / f"codex-runtime-recovery-{TARGET_CODEX_VERSION}.lock"
    return marker, lock


def _write_success_marker(marker: Path) -> None:
    temp = marker.with_suffix(".tmp")
    temp.write_text(f"version={TARGET_CODEX_VERSION}\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, marker)


def _rollback(
    npm_path: str,
    codex_path: str,
    old_version: str,
    environment: Mapping[str, str],
) -> bool:
    if not _install_version(npm_path, old_version, environment):
        return False
    try:
        return _codex_version(codex_path, environment) == old_version
    except CodexRuntimeRecoveryError:
        return False


def ensure_pinned_codex_runtime(environment: Mapping[str, str]) -> bool:
    """Pin and verify one exact Codex version; restore prior version on client failure."""
    npm_path, codex_path = _global_runtime_paths(environment)
    marker, lock_path = _state_paths(environment)

    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(lock_path, 0o600)
    with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

        old_version = _codex_version(codex_path, environment)
        if old_version == TARGET_CODEX_VERSION and marker.is_file():
            return True

        if marker.exists():
            marker.unlink()

        mutation_attempted = old_version != TARGET_CODEX_VERSION
        if mutation_attempted:
            if not _install_version(npm_path, TARGET_CODEX_VERSION, environment):
                _rollback(npm_path, codex_path, old_version, environment)
                return False
            try:
                installed_version = _codex_version(codex_path, environment)
            except CodexRuntimeRecoveryError:
                _rollback(npm_path, codex_path, old_version, environment)
                return False
            if installed_version != TARGET_CODEX_VERSION:
                _rollback(npm_path, codex_path, old_version, environment)
                return False

        smoke_status = _smoke_codex(codex_path, environment)
        if smoke_status == _SMOKE_FAILED:
            if mutation_attempted:
                _rollback(npm_path, codex_path, old_version, environment)
            return False

        # Provider quota/outage occurs after the client has reached normal provider
        # handling. It must not roll a schema-compatible pinned client back to the
        # known-bad prior runtime. The independent codegen canary may then use the
        # separately bounded provider-outage fallback policy.
        _write_success_marker(marker)
        return True
