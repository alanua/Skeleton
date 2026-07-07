from __future__ import annotations

from collections.abc import Mapping


HOME_EDGE_ENV_PREFIX = "SKELETON_HOME_EDGE_01_"


def sanitize_codegen_child_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Return a child-process environment without Home Edge runtime keys."""
    return {
        key: value
        for key, value in environment.items()
        if not key.startswith(HOME_EDGE_ENV_PREFIX)
    }
