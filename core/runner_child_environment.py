from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import stat
import subprocess

from core.codex_runtime_recovery import (
    CodexRuntimeRecoveryError,
    ensure_pinned_codex_runtime,
    is_canonical_systemd_runner_context,
    pinned_codex_recovery_marker_present,
    pinned_codex_runtime_path,
    should_attempt_codex_runtime_recovery,
)


HOME_EDGE_ENV_PREFIX = "SKELETON_HOME_EDGE_01_"
HOME_EDGE_EXEC_HMAC_SECRET_ENV = "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"
_FALLBACK_BIN_ENV = "SKELETON_CODEGEN_FALLBACK_BIN"
_REAL_CODEX_ENV = "SKELETON_REAL_CODEX_BIN"
_ORIGINAL_PATH_ENV = "SKELETON_CODEGEN_ORIGINAL_PATH"
_PROVIDER_OVERRIDE_ENV = frozenset(
    {
        "OPENROUTER_API_KEY",
        "BWS_ACCESS_TOKEN",
        "CREDENTIALS_DIRECTORY",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "MAX_BUDGET_PER_TASK",
        "MAX_ITERATIONS",
        "LLM_NUM_RETRIES",
        "SKELETON_OPENHANDS_BIN",
        "SKELETON_OPENHANDS_OPENROUTER_REQUIRED",
        "SKELETON_OPENROUTER_FALLBACK_API_KEY",
        "SKELETON_OPENROUTER_FALLBACK_MODEL",
        _FALLBACK_BIN_ENV,
        _REAL_CODEX_ENV,
        _ORIGINAL_PATH_ENV,
    }
)

_WRAPPER = r'''#!/usr/bin/env python3
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys

_DEFAULT_CODEX_MODEL = "gpt-5.6"


def _codex_args(argv: list[str]) -> list[str]:
    args = list(argv)
    if args and args[0] == "exec" and "--model" not in args:
        args[1:1] = ["--model", _DEFAULT_CODEX_MODEL]
    return args


def main() -> int:
    real_codex = os.environ.get("SKELETON_REAL_CODEX_BIN", "")
    original_path = os.environ.get(
        "SKELETON_CODEGEN_ORIGINAL_PATH", os.environ.get("PATH", "")
    )
    if not real_codex or not Path(real_codex).is_file():
        return 127

    stdin_text = sys.stdin.read()
    child_env = dict(os.environ)
    child_env["PATH"] = original_path
    for name in (
        "SKELETON_OPENHANDS_BIN",
        "SKELETON_OPENROUTER_FALLBACK_API_KEY",
        "SKELETON_OPENROUTER_FALLBACK_MODEL",
        "SKELETON_OPENHANDS_OPENROUTER_REQUIRED",
        "OPENROUTER_API_KEY",
        "BWS_ACCESS_TOKEN",
        "CREDENTIALS_DIRECTORY",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "MAX_BUDGET_PER_TASK",
        "MAX_ITERATIONS",
        "LLM_NUM_RETRIES",
    ):
        child_env.pop(name, None)

    codex = subprocess.run(
        [real_codex, *_codex_args(sys.argv[1:])],
        input=stdin_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        check=False,
    )
    if codex.returncode == 0:
        sys.stdout.write("SKELETON_CODEGEN_PROVIDER=codex\n")
    sys.stdout.write(codex.stdout)
    sys.stderr.write(codex.stderr)
    return codex.returncode


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _without_home_edge_credentials(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in environment.items()
        if not key.startswith(HOME_EDGE_ENV_PREFIX) and key != HOME_EDGE_EXEC_HMAC_SECRET_ENV
    }


def _without_codegen_secret_sources(environment: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in environment.items() if key not in _PROVIDER_OVERRIDE_ENV}


def _install_fallback_wrapper(
    environment: dict[str, str],
    authority_environment: Mapping[str, str],
) -> None:
    """Bind the Codex-only child wrapper from canonical recovered Runner authority.

    The historical helper/path name is retained for compatibility, but this wrapper
    has no executor/provider fallback. External executors require an explicit
    ExecutionBinding/RouteLease outside this environment shim.
    """
    if not is_canonical_systemd_runner_context(authority_environment):
        return
    if not pinned_codex_recovery_marker_present(authority_environment):
        return
    try:
        real_codex = pinned_codex_runtime_path(authority_environment)
    except (CodexRuntimeRecoveryError, OSError, subprocess.SubprocessError):
        return

    trusted_home = authority_environment.get("HOME", "").strip()
    trusted_path = authority_environment.get("PATH", "")
    if not trusted_home or not Path(trusted_home).is_absolute():
        return

    root = Path(trusted_home) / ".local" / "state" / "skeleton-runner" / "codegen-fallback-bin"
    wrapper = root / "codex"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    expected = _WRAPPER.encode("utf-8")
    current = wrapper.read_bytes() if wrapper.is_file() and not wrapper.is_symlink() else b""
    if hashlib.sha256(current).digest() != hashlib.sha256(expected).digest():
        tmp = root / ".codex.tmp"
        tmp.write_bytes(expected)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        os.replace(tmp, wrapper)
    else:
        os.chmod(wrapper, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    environment["HOME"] = trusted_home
    environment[_REAL_CODEX_ENV] = str(Path(real_codex).resolve(strict=False))
    environment[_ORIGINAL_PATH_ENV] = trusted_path
    environment[_FALLBACK_BIN_ENV] = str(root)
    environment["PATH"] = f"{root}:{trusted_path}" if trusted_path else str(root)


def sanitize_codegen_child_environment(
    environment: Mapping[str, str],
    *,
    authority_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment while keeping runtime authority in canonical Runner."""
    sanitized = _without_codegen_secret_sources(_without_home_edge_credentials(environment))
    authority = _without_home_edge_credentials(
        os.environ if authority_environment is None else authority_environment
    )
    if should_attempt_codex_runtime_recovery(authority):
        try:
            ensure_pinned_codex_runtime(authority)
        except (CodexRuntimeRecoveryError, OSError, subprocess.SubprocessError):
            pass
    try:
        _install_fallback_wrapper(sanitized, authority)
    except OSError:
        pass
    return sanitized
