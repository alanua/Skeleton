from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from core.private_memory import PRIVATE_MEMORY_CONFIG_ENV, PrivateMemoryConnector

# One authority only: explicit stack root or the configured authoritative SQLite parent.
# The configured file may retain its legacy private filename.
PRIVATE_MEMORY_ROOT_ENV = "SKELETON_RUNNER_PRIVATE_MEMORY_ROOT"
CANONICAL_DATABASE_NAME = "canonical.sqlite"


class PrivateMemoryRootResolutionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def resolve_private_memory_root(
    env: Mapping[str, str] | None = None,
    *,
    checkout: str | Path | None = None,
) -> tuple[str, str]:
    """Resolve one existing private stack root without creating a second authority."""

    values = dict(os.environ if env is None else env)
    explicit = values.get(PRIVATE_MEMORY_ROOT_ENV, "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        database = root / CANONICAL_DATABASE_NAME
        source = "explicit"
    else:
        if not values.get(PRIVATE_MEMORY_CONFIG_ENV, "").strip():
            raise PrivateMemoryRootResolutionError("private_memory_root_unavailable")
        try:
            connector = PrivateMemoryConnector(env=values)
            database = connector._load_db_path().expanduser().resolve()
        except Exception as exc:  # noqa: BLE001 - public caller uses bounded reason codes.
            raise PrivateMemoryRootResolutionError(
                "configured_private_memory_unavailable"
            ) from exc
        root = database.parent
        source = "canonical_config"

    if not root.is_absolute() or not root.is_dir():
        raise PrivateMemoryRootResolutionError("private_memory_root_unavailable")
    if _path_has_symlink_component(root):
        raise PrivateMemoryRootResolutionError("private_memory_root_unsafe")
    if not database.is_file():
        raise PrivateMemoryRootResolutionError("canonical_database_unavailable")

    if checkout is not None:
        checkout_path = Path(checkout).expanduser().resolve()
        if root == checkout_path or _is_relative_to(root, checkout_path):
            raise PrivateMemoryRootResolutionError("private_memory_root_inside_checkout")

    return str(root), source


def _path_has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
