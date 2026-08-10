from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
_METADATA_INCOMPATIBILITY_MARKERS = (
    "failed to decode models response",
    "unknown variant `max`",
    "unknown variant max",
)
_SMOKE_PASS = "pass"
_SMOKE_PROVIDER_UNAVAILABLE = "provider_unavailable"
_SMOKE_METADATA_INCOMPATIBLE = "metadata_incompatible"
_SMOKE_FAILED = "failed"


class CodexRuntimeRecoveryError(RuntimeError):
    """Raised when the bounded Codex runtime recovery cannot complete safely."""


@dataclass(frozen=True)
class CodexRuntimeRecoveryResult:
    success: bool
    reason: str


def should_attempt_codex_runtime_recovery(
    environment: Mapping[str, str],
    *,
    repository_root: Path | None = None,
    canonical_root: Path = CANONICAL_RUNNER_ROOT,
    enable_marker: Path | None = None,
) -> bool:
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
    result = _safe_run([codex_path, "--version"], environment, timeout=_VERSION_TIMEOUT_SECONDS)
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
    prefix_result = _safe_run([npm_path, "prefix", "-g"], environment, timeout=_VERSION_TIMEOUT_SECONDS)
    if prefix_result is None or prefix_result.returncode != 0 or not prefix_result.stdout.strip():
        raise CodexRuntimeRecoveryError("npm_global_prefix_unavailable")
    prefix = Path(prefix_result.stdout.strip()).expanduser().resolve(strict=False)
    return npm_path, str((prefix / "bin" / "codex").resolve(strict=False))


def pinned_codex_runtime_path(environment: Mapping[str, str]) -> str:
    _npm_path, codex_path = _global_runtime_paths(environment)
    if _codex_version(codex_path, environment) != TARGET_CODEX_VERSION:
        raise CodexRuntimeRecoveryError("codex_runtime_version_mismatch")
    return codex_path


def _state_dir(environment: Mapping[str, str]) -> Path:
    home_text = environment.get("HOME", "").strip()
    if not home_text:
        raise CodexRuntimeRecoveryError("runner_home_missing")
    home = Path(home_text).expanduser()
    if not home.is_absolute():
        raise CodexRuntimeRecoveryError("runner_home_not_absolute")
    return home / ".local" / "state" / "skeleton"


def pinned_codex_recovery_marker_present(environment: Mapping[str, str]) -> bool:
    try:
        marker = _state_dir(environment) / f"codex-runtime-recovery-{TARGET_CODEX_VERSION}.ok"
        if marker.is_symlink() or not marker.is_file():
            return False
        return marker.read_text(encoding="utf-8") == f"version={TARGET_CODEX_VERSION}\n"
    except (CodexRuntimeRecoveryError, OSError, UnicodeError):
        return False


def _install_version(npm_path: str, version: str, environment: Mapping[str, str]) -> bool:
    result = _safe_run(
        [npm_path, "install", "-g", f"@openai/codex@{version}", "--no-audit", "--no-fund"],
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
        if result.returncode == 0 and "RESULT: OK" in {line.strip() for line in result.stdout.splitlines()}:
            return _SMOKE_PASS
        combined = f"{result.stdout}\n{result.stderr}".lower()
        if any(marker in combined for marker in _METADATA_INCOMPATIBILITY_MARKERS):
            return _SMOKE_METADATA_INCOMPATIBLE
        if any(marker in combined for marker in _PROVIDER_OUTAGE_MARKERS):
            return _SMOKE_PROVIDER_UNAVAILABLE
        return _SMOKE_FAILED


def _state_paths(environment: Mapping[str, str]) -> tuple[Path, Path]:
    state_dir = _state_dir(environment)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return (
        state_dir / f"codex-runtime-recovery-{TARGET_CODEX_VERSION}.ok",
        state_dir / f"codex-runtime-recovery-{TARGET_CODEX_VERSION}.lock",
    )


def _write_success_marker(marker: Path) -> None:
    temp = marker.with_suffix(".tmp")
    temp.write_text(f"version={TARGET_CODEX_VERSION}\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, marker)


def _rollback(npm_path: str, codex_path: str, old_version: str, environment: Mapping[str, str]) -> bool:
    if not _install_version(npm_path, old_version, environment):
        return False
    try:
        return _codex_version(codex_path, environment) == old_version
    except CodexRuntimeRecoveryError:
        return False


def _failure(reason: str, *, rollback_ok: bool | None = None) -> CodexRuntimeRecoveryResult:
    if rollback_ok is False:
        reason = f"{reason}_rollback_failed"
    return CodexRuntimeRecoveryResult(False, reason)


def recover_pinned_codex_runtime(environment: Mapping[str, str]) -> CodexRuntimeRecoveryResult:
    """Pin the exact client and return only a stable public-safe recovery phase/reason."""
    try:
        npm_path, codex_path = _global_runtime_paths(environment)
        marker, lock_path = _state_paths(environment)
    except CodexRuntimeRecoveryError as exc:
        return _failure(str(exc))
    except OSError:
        return _failure("recovery_state_unavailable")

    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(lock_path, 0o600)
    except OSError:
        return _failure("recovery_lock_unavailable")

    with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            old_version = _codex_version(codex_path, environment)
        except CodexRuntimeRecoveryError as exc:
            return _failure(f"existing_{exc}")

        if old_version == TARGET_CODEX_VERSION and marker.is_file():
            return CodexRuntimeRecoveryResult(True, "already_ready")
        if marker.exists():
            try:
                marker.unlink()
            except OSError:
                return _failure("stale_marker_remove_failed")

        mutation_attempted = old_version != TARGET_CODEX_VERSION
        if mutation_attempted:
            if not _install_version(npm_path, TARGET_CODEX_VERSION, environment):
                rollback_ok = _rollback(npm_path, codex_path, old_version, environment)
                return _failure("target_install_failed", rollback_ok=rollback_ok)
            try:
                installed_version = _codex_version(codex_path, environment)
            except CodexRuntimeRecoveryError as exc:
                rollback_ok = _rollback(npm_path, codex_path, old_version, environment)
                return _failure(f"installed_{exc}", rollback_ok=rollback_ok)
            if installed_version != TARGET_CODEX_VERSION:
                rollback_ok = _rollback(npm_path, codex_path, old_version, environment)
                return _failure("installed_version_mismatch", rollback_ok=rollback_ok)

        smoke_status = _smoke_codex(codex_path, environment)
        if smoke_status in {_SMOKE_FAILED, _SMOKE_METADATA_INCOMPATIBLE}:
            rollback_ok = None
            if mutation_attempted:
                rollback_ok = _rollback(npm_path, codex_path, old_version, environment)
            reason = (
                "smoke_metadata_incompatible"
                if smoke_status == _SMOKE_METADATA_INCOMPATIBLE
                else "smoke_client_failed"
            )
            return _failure(reason, rollback_ok=rollback_ok)

        try:
            _write_success_marker(marker)
        except OSError:
            rollback_ok = None
            if mutation_attempted:
                rollback_ok = _rollback(npm_path, codex_path, old_version, environment)
            return _failure("success_marker_write_failed", rollback_ok=rollback_ok)

        if smoke_status == _SMOKE_PROVIDER_UNAVAILABLE:
            return CodexRuntimeRecoveryResult(True, "ready_provider_unavailable")
        return CodexRuntimeRecoveryResult(True, "ready")


def ensure_pinned_codex_runtime(environment: Mapping[str, str]) -> bool:
    """Compatibility wrapper for callers that only need success/failure."""
    return recover_pinned_codex_runtime(environment).success
