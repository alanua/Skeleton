from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import shutil
import stat
import subprocess

from core.codex_runtime_recovery import (
    CodexRuntimeRecoveryError,
    ensure_pinned_codex_runtime,
    should_attempt_codex_runtime_recovery,
)


HOME_EDGE_ENV_PREFIX = "SKELETON_HOME_EDGE_01_"
HOME_EDGE_EXEC_HMAC_SECRET_ENV = "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"
CODEGEN_PROVIDER_SHIM_DIR_ENV = "SKELETON_CODEGEN_PROVIDER_SHIM_DIR"
REAL_CODEX_ENV = "SKELETON_REAL_CODEX_BIN"
OPENHANDS_ENV = "SKELETON_OPENHANDS_BIN"

_SHIM = r'''#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

_QUOTA_MARKERS = (
    "you've hit your usage limit",
    "usage limit",
    "insufficient_quota",
    "rate limit",
    "service unavailable",
    "temporarily unavailable",
)


def _workdir(argv: list[str]) -> str:
    try:
        index = argv.index("--cd")
        return argv[index + 1]
    except (ValueError, IndexError):
        return os.getcwd()


def main() -> int:
    real_codex = os.environ.get("SKELETON_REAL_CODEX_BIN", "")
    openhands = os.environ.get("SKELETON_OPENHANDS_BIN", "")
    if not real_codex:
        print("provider-shim: real Codex path unavailable", file=sys.stderr)
        return 127

    argv = sys.argv[1:]
    stdin_text = sys.stdin.read()
    codex = subprocess.run(
        [real_codex, *argv],
        input=stdin_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if codex.returncode == 0:
        sys.stdout.write(codex.stdout)
        sys.stderr.write(codex.stderr)
        return 0

    combined = (codex.stdout + "\n" + codex.stderr).lower()
    may_fallback = bool(openhands) and any(marker in combined for marker in _QUOTA_MARKERS)
    if not may_fallback:
        sys.stdout.write(codex.stdout)
        sys.stderr.write(codex.stderr)
        return codex.returncode

    workdir = _workdir(argv)
    if not Path(workdir).is_dir():
        sys.stderr.write(codex.stderr)
        print("provider-shim: bounded workdir unavailable", file=sys.stderr)
        return codex.returncode
    if not stdin_text.strip():
        sys.stderr.write(codex.stderr)
        print("provider-shim: empty task cannot fall back", file=sys.stderr)
        return codex.returncode

    print("provider_fallback=openhands", file=sys.stderr)
    fallback = subprocess.run(
        [openhands, "--headless", "--json", "-t", stdin_text],
        cwd=workdir,
        text=True,
        check=False,
    )
    return fallback.returncode


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _install_provider_shim(environment: dict[str, str]) -> None:
    original_path = environment.get("PATH", "")
    real_codex = shutil.which("codex", path=original_path)
    openhands = shutil.which("openhands", path=original_path)
    if real_codex is None or openhands is None:
        return

    configured_dir = environment.get(CODEGEN_PROVIDER_SHIM_DIR_ENV, "").strip()
    shim_dir = Path(configured_dir or "/home/agent/.local/state/skeleton-runner/provider-shim")
    shim_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    shim_path = shim_dir / "codex"
    tmp_path = shim_dir / ".codex.tmp"
    tmp_path.write_text(_SHIM, encoding="utf-8")
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    os.replace(tmp_path, shim_path)

    environment[REAL_CODEX_ENV] = real_codex
    environment[OPENHANDS_ENV] = openhands
    environment["PATH"] = f"{shim_dir}:{original_path}" if original_path else str(shim_dir)


def sanitize_codegen_child_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Return a child-process environment without Home Edge runtime keys."""
    sanitized = {
        key: value
        for key, value in environment.items()
        if (
            not key.startswith(HOME_EDGE_ENV_PREFIX)
            and key != HOME_EDGE_EXEC_HMAC_SECRET_ENV
        )
    }
    if should_attempt_codex_runtime_recovery(sanitized):
        try:
            ensure_pinned_codex_runtime(sanitized)
        except (CodexRuntimeRecoveryError, OSError, subprocess.SubprocessError):
            pass
    _install_provider_shim(sanitized)
    return sanitized
