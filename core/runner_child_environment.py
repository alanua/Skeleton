from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess

from core.codex_runtime_recovery import (
    CodexRuntimeRecoveryError,
    ensure_pinned_codex_runtime,
    pinned_codex_recovery_marker_present,
    pinned_codex_runtime_path,
    should_attempt_codex_runtime_recovery,
)


HOME_EDGE_ENV_PREFIX = "SKELETON_HOME_EDGE_01_"
HOME_EDGE_EXEC_HMAC_SECRET_ENV = "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"
_FALLBACK_BIN_ENV = "SKELETON_CODEGEN_FALLBACK_BIN"
_REAL_CODEX_ENV = "SKELETON_REAL_CODEX_BIN"
_OPENHANDS_ENV = "SKELETON_OPENHANDS_BIN"
_ORIGINAL_PATH_ENV = "SKELETON_CODEGEN_ORIGINAL_PATH"

_WRAPPER = r'''#!/usr/bin/env python3
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys

_MARKERS = (
    "usage limit",
    "rate limit",
    "quota",
    "insufficient_quota",
    "provider unavailable",
    "temporarily unavailable",
    "service unavailable",
    "try again at",
)


def _quota_or_provider_outage(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _MARKERS)


def _fallback_allowed(text: str) -> bool:
    return _quota_or_provider_outage(text)


def _workdir(argv: list[str]) -> str:
    if "--cd" in argv:
        index = argv.index("--cd")
        if index + 1 < len(argv):
            candidate = Path(argv[index + 1]).resolve(strict=False)
            if candidate.is_dir():
                return str(candidate)
    return os.getcwd()


def _task(argv: list[str], stdin_text: str) -> str:
    text = stdin_text.strip()
    if text and text != "-":
        return text
    for item in reversed(argv):
        if item != "-" and not item.startswith("-") and item not in {"workspace-write", "read-only"}:
            return item
    return "Complete the bounded Runner task in the current worktree and run its required validation."


def main() -> int:
    real_codex = os.environ.get("SKELETON_REAL_CODEX_BIN", "")
    openhands = os.environ.get("SKELETON_OPENHANDS_BIN", "")
    original_path = os.environ.get("SKELETON_CODEGEN_ORIGINAL_PATH", os.environ.get("PATH", ""))
    if not real_codex or not Path(real_codex).is_file():
        return 127
    stdin_text = sys.stdin.read()
    child_env = dict(os.environ)
    child_env["PATH"] = original_path
    codex = subprocess.run(
        [real_codex, *sys.argv[1:]],
        input=stdin_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        check=False,
    )
    if codex.returncode == 0:
        sys.stdout.write(codex.stdout)
        sys.stderr.write(codex.stderr)
        return 0
    combined = f"{codex.stdout}\n{codex.stderr}"
    if not _fallback_allowed(combined) or not openhands or not Path(openhands).is_file():
        sys.stdout.write(codex.stdout)
        sys.stderr.write(codex.stderr)
        return codex.returncode
    fallback = subprocess.run(
        [openhands, "--headless", "--json", "-t", _task(sys.argv[1:], stdin_text)],
        cwd=_workdir(sys.argv[1:]),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        check=False,
    )
    if fallback.returncode == 0:
        sys.stdout.write("SKELETON_CODEGEN_PROVIDER=openhands\nRESULT: OK\n")
        return 0
    sys.stderr.write("SKELETON_CODEGEN_FALLBACK_FAILED\n")
    return fallback.returncode or codex.returncode


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _install_fallback_wrapper(environment: dict[str, str]) -> None:
    if not pinned_codex_recovery_marker_present(environment):
        return
    try:
        real_codex = pinned_codex_runtime_path(environment)
    except (CodexRuntimeRecoveryError, OSError, subprocess.SubprocessError):
        return
    original_path = environment.get("PATH", "")
    openhands = shutil.which("openhands", path=original_path)
    home = environment.get("HOME")
    if not home:
        return
    root = Path(home) / ".local" / "state" / "skeleton-runner" / "codegen-fallback-bin"
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
    environment[_REAL_CODEX_ENV] = str(Path(real_codex).resolve(strict=False))
    environment[_OPENHANDS_ENV] = str(Path(openhands).resolve(strict=False)) if openhands else ""
    environment[_ORIGINAL_PATH_ENV] = original_path
    environment[_FALLBACK_BIN_ENV] = str(root)
    environment["PATH"] = f"{root}:{original_path}" if original_path else str(root)


def sanitize_codegen_child_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Return the bounded codegen child environment with private Home Edge keys removed."""
    sanitized = {
        key: value
        for key, value in environment.items()
        if not key.startswith(HOME_EDGE_ENV_PREFIX) and key != HOME_EDGE_EXEC_HMAC_SECRET_ENV
    }
    if should_attempt_codex_runtime_recovery(sanitized):
        try:
            ensure_pinned_codex_runtime(sanitized)
        except (CodexRuntimeRecoveryError, OSError, subprocess.SubprocessError):
            pass
    try:
        _install_fallback_wrapper(sanitized)
    except OSError:
        pass
    return sanitized
