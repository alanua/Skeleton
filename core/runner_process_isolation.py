from __future__ import annotations

import math
import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

try:
    import resource
except ImportError:  # pragma: no cover - exercised only on unsupported platforms
    resource = None  # type: ignore[assignment]

from core.runner_task import TASK_KINDS


RUNNER_PROCESS_ISOLATION_VERSION: Final = (
    "skeleton.runner_process_isolation.v1"
)

ISOLATED_TASK_KINDS: Final = frozenset(
    {
        "code_edit",
        "repository_maintenance",
    }
)

MAX_ARGUMENTS: Final = 128
MAX_ARGUMENT_LENGTH: Final = 4096
MAX_TIMEOUT_SECONDS: Final = 3600.0
MAX_GRACE_SECONDS: Final = 30.0
MAX_OUTPUT_BYTES: Final = 16 * 1024 * 1024

_ENVIRONMENT_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")

_RESERVED_ENVIRONMENT_KEYS: Final = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "TMPDIR",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONNOUSERSITE",
    }
)

_BLOCKED_ENVIRONMENT_KEYS: Final = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "LD_AUDIT",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PYTHONHOME",
        "PYTHONPATH",
        "RUBYLIB",
    }
)


class RunnerProcessIsolationError(RuntimeError):
    """A subprocess request violated the Runner isolation boundary."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RunnerProcessLimits:
    cpu_seconds: int = 900
    address_space_bytes: int = 1024 * 1024 * 1024
    file_size_bytes: int = 64 * 1024 * 1024
    open_files: int = 128
    process_count: int = 64

    def __post_init__(self) -> None:
        _bounded_integer(
            self.cpu_seconds,
            "cpu_seconds",
            minimum=1,
            maximum=3600,
            reason_code="INVALID_RESOURCE_LIMIT",
        )
        _bounded_integer(
            self.address_space_bytes,
            "address_space_bytes",
            minimum=16 * 1024 * 1024,
            maximum=64 * 1024 * 1024 * 1024,
            reason_code="INVALID_RESOURCE_LIMIT",
        )
        _bounded_integer(
            self.file_size_bytes,
            "file_size_bytes",
            minimum=1024,
            maximum=1024 * 1024 * 1024,
            reason_code="INVALID_RESOURCE_LIMIT",
        )
        _bounded_integer(
            self.open_files,
            "open_files",
            minimum=16,
            maximum=4096,
            reason_code="INVALID_RESOURCE_LIMIT",
        )
        _bounded_integer(
            self.process_count,
            "process_count",
            minimum=1,
            maximum=1024,
            reason_code="INVALID_RESOURCE_LIMIT",
        )


@dataclass(frozen=True)
class RunnerProcessResult:
    argv: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    sigterm_sent: bool
    sigkill_sent: bool
    duration_seconds: float


class RunnerProcessIsolator:
    """POSIX subprocess boundary for the two high-risk Runner routes."""

    def __init__(
        self,
        allowed_executables: Iterable[str | os.PathLike[str]],
        *,
        git_executable: str | os.PathLike[str],
        allowed_environment_keys: Iterable[str] = (),
        limits: RunnerProcessLimits | None = None,
        output_limit_bytes: int = 1024 * 1024,
        termination_grace_seconds: float = 2.0,
    ) -> None:
        _ensure_supported_platform()

        if limits is None:
            limits = RunnerProcessLimits()

        if not isinstance(limits, RunnerProcessLimits):
            raise RunnerProcessIsolationError(
                "INVALID_RESOURCE_LIMITS",
                "limits must be a RunnerProcessLimits value",
            )

        self._allowed_executables = _normalize_executable_allowlist(
            allowed_executables
        )
        self._git_executable = _normalize_executable(
            git_executable,
            reason_prefix="GIT",
        )
        self._allowed_environment_keys = _normalize_environment_allowlist(
            allowed_environment_keys
        )
        self._limits = limits
        self._output_limit_bytes = _bounded_integer(
            output_limit_bytes,
            "output_limit_bytes",
            minimum=1,
            maximum=MAX_OUTPUT_BYTES,
            reason_code="INVALID_ISOLATION_SETTING",
        )
        self._termination_grace_seconds = _bounded_number(
            termination_grace_seconds,
            "termination_grace_seconds",
            minimum=0.0,
            maximum=MAX_GRACE_SECONDS,
        )

        if limits.file_size_bytes <= self._output_limit_bytes:
            raise RunnerProcessIsolationError(
                "OUTPUT_LIMIT_EXCEEDS_FILE_LIMIT",
                "file size limit must exceed the output capture limit",
            )

    @property
    def allowed_executables(self) -> tuple[str, ...]:
        return tuple(str(path) for path in self._allowed_executables)

    def run(
        self,
        *,
        task_kind: object,
        argv: object,
        worktree_root: str | os.PathLike[str],
        cwd: str | os.PathLike[str] | None = None,
        timeout_seconds: object,
        environment: Mapping[str, str] | None = None,
    ) -> RunnerProcessResult:
        _require_isolated_route(task_kind)

        command = _normalize_command(
            argv,
            self._allowed_executables,
        )
        _, resolved_cwd = _verified_worktree(
            worktree_root,
            cwd,
            self._git_executable,
        )
        timeout = _bounded_number(
            timeout_seconds,
            "timeout_seconds",
            minimum=0.001,
            maximum=MAX_TIMEOUT_SECONDS,
        )
        explicit_environment = _normalize_environment(
            environment,
            self._allowed_environment_keys,
        )

        started = time.monotonic()
        timed_out = False
        sigterm_sent = False
        sigkill_sent = False
        process: subprocess.Popen[bytes] | None = None

        with tempfile.TemporaryDirectory(
            prefix="skeleton-runner-isolated-"
        ) as scratch:
            child_environment = _minimal_environment(
                Path(scratch),
                explicit_environment,
            )

            with tempfile.TemporaryFile(mode="w+b") as stdout_file:
                with tempfile.TemporaryFile(mode="w+b") as stderr_file:
                    try:
                        process = subprocess.Popen(
                            command,
                            cwd=str(resolved_cwd),
                            env=child_environment,
                            stdin=subprocess.DEVNULL,
                            stdout=stdout_file,
                            stderr=stderr_file,
                            shell=False,
                            close_fds=True,
                            start_new_session=True,
                            preexec_fn=_resource_limiter(self._limits),
                        )

                        try:
                            returncode = process.wait(timeout=timeout)
                        except subprocess.TimeoutExpired:
                            timed_out = True
                            sigterm_sent = _signal_process_group(
                                process.pid,
                                signal.SIGTERM,
                            )

                            try:
                                returncode = process.wait(
                                    timeout=self._termination_grace_seconds
                                )
                            except subprocess.TimeoutExpired:
                                sigkill_sent = _signal_process_group(
                                    process.pid,
                                    signal.SIGKILL,
                                )
                                returncode = process.wait()

                    except (OSError, subprocess.SubprocessError) as exc:
                        raise RunnerProcessIsolationError(
                            "PROCESS_START_FAILED",
                            "isolated subprocess could not be started",
                        ) from exc
                    finally:
                        if process is not None and process.poll() is None:
                            try:
                                sigkill_sent = (
                                    _signal_process_group(
                                        process.pid,
                                        signal.SIGKILL,
                                    )
                                    or sigkill_sent
                                )
                            finally:
                                process.wait()

                        if process is not None:
                            residual_term, residual_kill = (
                                _cleanup_residual_process_group(
                                    process.pid,
                                    self._termination_grace_seconds,
                                )
                            )
                            sigterm_sent = (
                                sigterm_sent or residual_term
                            )
                            sigkill_sent = (
                                sigkill_sent or residual_kill
                            )

                    stdout = _read_bounded_output(
                        stdout_file,
                        self._output_limit_bytes,
                        "stdout",
                    )
                    stderr = _read_bounded_output(
                        stderr_file,
                        self._output_limit_bytes,
                        "stderr",
                    )

        return RunnerProcessResult(
            argv=command,
            cwd=str(resolved_cwd),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            sigterm_sent=sigterm_sent,
            sigkill_sent=sigkill_sent,
            duration_seconds=max(0.0, time.monotonic() - started),
        )


def requires_process_isolation(task_kind: object) -> bool:
    if not isinstance(task_kind, str) or task_kind not in TASK_KINDS:
        raise RunnerProcessIsolationError(
            "UNKNOWN_ISOLATION_ROUTE",
            "task kind is not a registered semantic Runner route",
        )
    return task_kind in ISOLATED_TASK_KINDS


def _require_isolated_route(task_kind: object) -> str:
    if not isinstance(task_kind, str) or task_kind not in TASK_KINDS:
        raise RunnerProcessIsolationError(
            "UNKNOWN_ISOLATION_ROUTE",
            "task kind is not a registered semantic Runner route",
        )

    if task_kind not in ISOLATED_TASK_KINDS:
        raise RunnerProcessIsolationError(
            "ROUTE_NOT_ISOLATED",
            "this semantic route may not use the isolation boundary",
        )

    return task_kind


def _ensure_supported_platform() -> None:
    required_limits = (
        "RLIMIT_AS",
        "RLIMIT_CORE",
        "RLIMIT_CPU",
        "RLIMIT_FSIZE",
        "RLIMIT_NOFILE",
        "RLIMIT_NPROC",
    )

    if os.name != "posix" or not hasattr(os, "killpg"):
        raise RunnerProcessIsolationError(
            "UNSUPPORTED_ISOLATION_PLATFORM",
            "Runner process isolation requires POSIX process groups",
        )

    if resource is None or any(
        not hasattr(resource, name) for name in required_limits
    ):
        raise RunnerProcessIsolationError(
            "UNSUPPORTED_ISOLATION_PLATFORM",
            "required POSIX resource limits are unavailable",
        )


def _normalize_executable_allowlist(
    values: Iterable[str | os.PathLike[str]],
) -> tuple[Path, ...]:
    if isinstance(values, (str, bytes, bytearray, os.PathLike)):
        raise RunnerProcessIsolationError(
            "INVALID_EXECUTABLE_ALLOWLIST",
            "allowed executables must be an iterable of absolute paths",
        )

    try:
        candidates = tuple(values)
    except TypeError as exc:
        raise RunnerProcessIsolationError(
            "INVALID_EXECUTABLE_ALLOWLIST",
            "allowed executables must be iterable",
        ) from exc

    if not candidates:
        raise RunnerProcessIsolationError(
            "EMPTY_EXECUTABLE_ALLOWLIST",
            "at least one executable must be allowlisted",
        )

    normalized = tuple(
        _normalize_executable(candidate, reason_prefix="EXECUTABLE")
        for candidate in candidates
    )

    if len(set(normalized)) != len(normalized):
        raise RunnerProcessIsolationError(
            "DUPLICATE_EXECUTABLE_ALLOWLIST_ENTRY",
            "executable allowlist contains duplicate resolved paths",
        )

    return tuple(sorted(normalized, key=str))


def _normalize_executable(
    value: str | os.PathLike[str],
    *,
    reason_prefix: str,
) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise RunnerProcessIsolationError(
            f"INVALID_{reason_prefix}_EXECUTABLE",
            "executable must be a filesystem path",
        ) from exc

    if not path.is_absolute():
        raise RunnerProcessIsolationError(
            f"{reason_prefix}_EXECUTABLE_NOT_ABSOLUTE",
            "executable path must be absolute",
        )

    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RunnerProcessIsolationError(
            f"{reason_prefix}_EXECUTABLE_NOT_FOUND",
            "executable does not exist",
        ) from exc

    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RunnerProcessIsolationError(
            f"{reason_prefix}_EXECUTABLE_NOT_RUNNABLE",
            "executable must be a runnable file",
        )

    return resolved


def _normalize_command(
    value: object,
    allowed_executables: tuple[Path, ...],
) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
    ):
        raise RunnerProcessIsolationError(
            "INVALID_PROCESS_COMMAND",
            "argv must be a sequence of command arguments",
        )

    arguments = tuple(value)

    if not arguments or len(arguments) > MAX_ARGUMENTS:
        raise RunnerProcessIsolationError(
            "INVALID_PROCESS_COMMAND",
            "argv must contain a bounded non-empty command",
        )

    if any(
        not isinstance(argument, str)
        or not argument
        or "\x00" in argument
        or len(argument) > MAX_ARGUMENT_LENGTH
        for argument in arguments
    ):
        raise RunnerProcessIsolationError(
            "INVALID_PROCESS_ARGUMENT",
            "command arguments must be bounded non-empty strings",
        )

    executable = Path(arguments[0])

    if not executable.is_absolute():
        raise RunnerProcessIsolationError(
            "EXECUTABLE_PATH_NOT_ABSOLUTE",
            "argv[0] must be an absolute executable path",
        )

    try:
        resolved = executable.resolve(strict=True)
    except OSError as exc:
        raise RunnerProcessIsolationError(
            "EXECUTABLE_NOT_FOUND",
            "requested executable does not exist",
        ) from exc

    if resolved not in allowed_executables:
        raise RunnerProcessIsolationError(
            "EXECUTABLE_NOT_ALLOWLISTED",
            "requested executable is not in the exact allowlist",
        )

    return (str(resolved), *arguments[1:])


def _verified_worktree(
    worktree_root: str | os.PathLike[str],
    cwd: str | os.PathLike[str] | None,
    git_executable: Path,
) -> tuple[Path, Path]:
    try:
        root_candidate = Path(worktree_root)
    except TypeError as exc:
        raise RunnerProcessIsolationError(
            "INVALID_WORKTREE_ROOT",
            "worktree root must be a path",
        ) from exc

    if not root_candidate.is_absolute():
        raise RunnerProcessIsolationError(
            "WORKTREE_ROOT_NOT_ABSOLUTE",
            "worktree root must be absolute",
        )

    try:
        root = root_candidate.resolve(strict=True)
    except OSError as exc:
        raise RunnerProcessIsolationError(
            "WORKTREE_ROOT_NOT_FOUND",
            "worktree root does not exist",
        ) from exc

    if not root.is_dir():
        raise RunnerProcessIsolationError(
            "INVALID_WORKTREE_ROOT",
            "worktree root must be a directory",
        )

    git_marker = root / ".git"

    if git_marker.is_symlink() or not (
        git_marker.is_file() or git_marker.is_dir()
    ):
        raise RunnerProcessIsolationError(
            "UNVERIFIED_WORKTREE_ROOT",
            "worktree root must contain a non-symlink .git marker",
        )

    try:
        check = subprocess.run(
            (
                str(git_executable),
                "-C",
                str(root),
                "rev-parse",
                "--show-toplevel",
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "HOME": "/nonexistent",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            },
            shell=False,
            close_fds=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunnerProcessIsolationError(
            "WORKTREE_VERIFICATION_FAILED",
            "Git worktree verification could not be executed",
        ) from exc

    if check.returncode != 0:
        raise RunnerProcessIsolationError(
            "UNVERIFIED_WORKTREE_ROOT",
            "Git did not recognize the requested worktree root",
        )

    try:
        reported_root = Path(
            check.stdout.decode("utf-8").strip()
        ).resolve(strict=True)
    except (OSError, UnicodeDecodeError) as exc:
        raise RunnerProcessIsolationError(
            "WORKTREE_VERIFICATION_FAILED",
            "Git returned an invalid worktree path",
        ) from exc

    if reported_root != root:
        raise RunnerProcessIsolationError(
            "WORKTREE_ROOT_MISMATCH",
            "requested worktree root is not the Git top-level directory",
        )

    try:
        cwd_candidate = root if cwd is None else Path(cwd)
    except TypeError as exc:
        raise RunnerProcessIsolationError(
            "INVALID_PROCESS_CWD",
            "subprocess cwd must be a path",
        ) from exc

    if not cwd_candidate.is_absolute():
        raise RunnerProcessIsolationError(
            "PROCESS_CWD_NOT_ABSOLUTE",
            "subprocess cwd must be absolute",
        )

    try:
        resolved_cwd = cwd_candidate.resolve(strict=True)
    except OSError as exc:
        raise RunnerProcessIsolationError(
            "PROCESS_CWD_NOT_FOUND",
            "subprocess cwd does not exist",
        ) from exc

    if not resolved_cwd.is_dir():
        raise RunnerProcessIsolationError(
            "INVALID_PROCESS_CWD",
            "subprocess cwd must be a directory",
        )

    if resolved_cwd != root and root not in resolved_cwd.parents:
        raise RunnerProcessIsolationError(
            "PROCESS_CWD_ESCAPES_WORKTREE",
            "subprocess cwd must remain inside the verified worktree",
        )

    return root, resolved_cwd


def _normalize_environment_allowlist(
    values: Iterable[str],
) -> frozenset[str]:
    if isinstance(values, (str, bytes, bytearray)):
        raise RunnerProcessIsolationError(
            "INVALID_ENVIRONMENT_ALLOWLIST",
            "environment allowlist must be an iterable",
        )

    try:
        keys = tuple(values)
    except TypeError as exc:
        raise RunnerProcessIsolationError(
            "INVALID_ENVIRONMENT_ALLOWLIST",
            "environment allowlist must be iterable",
        ) from exc

    if any(
        not isinstance(key, str)
        or not _ENVIRONMENT_KEY_RE.fullmatch(key)
        for key in keys
    ):
        raise RunnerProcessIsolationError(
            "INVALID_ENVIRONMENT_KEY",
            "environment keys must use safe uppercase names",
        )

    if len(set(keys)) != len(keys):
        raise RunnerProcessIsolationError(
            "DUPLICATE_ENVIRONMENT_KEY",
            "environment allowlist contains duplicates",
        )

    forbidden = set(keys) & (
        _RESERVED_ENVIRONMENT_KEYS | _BLOCKED_ENVIRONMENT_KEYS
    )

    if forbidden:
        raise RunnerProcessIsolationError(
            "FORBIDDEN_ENVIRONMENT_KEY",
            f"environment key cannot be allowlisted: "
            f"{sorted(forbidden)[0]}",
        )

    return frozenset(keys)


def _normalize_environment(
    value: Mapping[str, str] | None,
    allowed_keys: frozenset[str],
) -> dict[str, str]:
    if value is None:
        return {}

    if not isinstance(value, Mapping):
        raise RunnerProcessIsolationError(
            "INVALID_PROCESS_ENVIRONMENT",
            "process environment must be an explicit mapping",
        )

    normalized: dict[str, str] = {}

    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not _ENVIRONMENT_KEY_RE.fullmatch(key)
        ):
            raise RunnerProcessIsolationError(
                "INVALID_ENVIRONMENT_KEY",
                "environment keys must use safe uppercase names",
            )

        if key not in allowed_keys:
            raise RunnerProcessIsolationError(
                "ENVIRONMENT_KEY_NOT_ALLOWLISTED",
                f"environment key is not allowlisted: {key}",
            )

        if (
            not isinstance(item, str)
            or "\x00" in item
            or len(item) > 8192
        ):
            raise RunnerProcessIsolationError(
                "INVALID_ENVIRONMENT_VALUE",
                "environment values must be bounded strings",
            )

        normalized[key] = item

    return normalized


def _minimal_environment(
    scratch: Path,
    explicit: Mapping[str, str],
) -> dict[str, str]:
    environment = {
        "HOME": str(scratch),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "",
        "TMPDIR": str(scratch),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    environment.update(explicit)
    return environment


def _resource_limiter(limits: RunnerProcessLimits):
    def apply_limits() -> None:
        assert resource is not None

        os.umask(0o077)

        _set_resource_limit(resource.RLIMIT_CORE, 0)
        _set_resource_limit(resource.RLIMIT_CPU, limits.cpu_seconds)
        _set_resource_limit(
            resource.RLIMIT_AS,
            limits.address_space_bytes,
        )
        _set_resource_limit(
            resource.RLIMIT_FSIZE,
            limits.file_size_bytes,
        )
        _set_resource_limit(
            resource.RLIMIT_NOFILE,
            limits.open_files,
        )
        _set_resource_limit(
            resource.RLIMIT_NPROC,
            limits.process_count,
        )

    return apply_limits


def _set_resource_limit(resource_name: int, requested: int) -> None:
    assert resource is not None

    _, hard_limit = resource.getrlimit(resource_name)
    effective = requested

    if hard_limit != resource.RLIM_INFINITY:
        effective = min(effective, int(hard_limit))

    resource.setrlimit(
        resource_name,
        (effective, effective),
    )


def _signal_process_group(
    pid: int,
    requested_signal: signal.Signals,
) -> bool:
    try:
        os.killpg(pid, requested_signal)
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise RunnerProcessIsolationError(
            "PROCESS_GROUP_CLEANUP_FAILED",
            "isolated process group could not be terminated",
        ) from exc

    return True


def _process_group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise RunnerProcessIsolationError(
            "PROCESS_GROUP_CLEANUP_FAILED",
            "isolated process group state could not be checked",
        ) from exc

    return True


def _cleanup_residual_process_group(
    pid: int,
    grace_seconds: float,
) -> tuple[bool, bool]:
    if not _process_group_exists(pid):
        return False, False

    sigterm_sent = _signal_process_group(
        pid,
        signal.SIGTERM,
    )

    deadline = time.monotonic() + grace_seconds

    while (
        _process_group_exists(pid)
        and time.monotonic() < deadline
    ):
        time.sleep(0.02)

    if not _process_group_exists(pid):
        return sigterm_sent, False

    sigkill_sent = _signal_process_group(
        pid,
        signal.SIGKILL,
    )

    return sigterm_sent, sigkill_sent


def _read_bounded_output(
    file_object,
    limit: int,
    stream_name: str,
) -> str:
    file_object.flush()
    file_object.seek(0)
    payload = file_object.read(limit + 1)

    if len(payload) > limit:
        raise RunnerProcessIsolationError(
            "PROCESS_OUTPUT_LIMIT_EXCEEDED",
            f"isolated process {stream_name} exceeded the limit",
        )

    return payload.decode("utf-8", errors="replace")


def _bounded_integer(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
    reason_code: str,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise RunnerProcessIsolationError(
            reason_code,
            f"{field} must be between {minimum} and {maximum}",
        )

    return value


def _bounded_number(
    value: object,
    field: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
    ):
        raise RunnerProcessIsolationError(
            "INVALID_ISOLATION_SETTING",
            f"{field} must be numeric",
        )

    normalized = float(value)

    if (
        not math.isfinite(normalized)
        or normalized < minimum
        or normalized > maximum
    ):
        raise RunnerProcessIsolationError(
            "INVALID_ISOLATION_SETTING",
            f"{field} is outside the allowed range",
        )

    return normalized
