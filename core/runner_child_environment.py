from __future__ import annotations

from collections.abc import Mapping
import subprocess

from core.codex_runtime_recovery import (
    CodexRuntimeRecoveryError,
    ensure_pinned_codex_runtime,
    should_attempt_codex_runtime_recovery,
)


HOME_EDGE_ENV_PREFIX = "SKELETON_HOME_EDGE_01_"
HOME_EDGE_EXEC_HMAC_SECRET_ENV = "SKELETON_HOME_EDGE_EXEC_HMAC_SECRET"


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
            # The recovery helper captures child output and rolls back on mutation failure.
            # Existing code-lane handling remains the public-safe fallback if recovery fails.
            pass
    return sanitized
