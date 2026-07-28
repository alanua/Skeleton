from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from core.private_memory import PRIVATE_MEMORY_CONFIG_ENV, PrivateMemoryConnector


PRIVATE_MEMORY_ROOT_ENV = "SKELETON_RUNNER_PRIVATE_MEMORY_ROOT"
PRIVATE_MEMORY_STACK_ROOT_ENV = "SKELETON_PRIVATE_MEMORY_ROOT"
CANONICAL_DATABASE_NAME = "canonical.sqlite"
DEFAULT_CANONICAL_STACK_RELATIVE = Path(".local/share/skeleton-private-memory")


class PrivateMemoryRootResolutionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def resolve_private_memory_root(
    env: Mapping[str, str] | None = None,
    *,
    checkout: str | Path | None = None,
) -> tuple[str, str]:
    """Resolve the broader private runtime root used by existing memory services.

    A legacy connector database may anchor this directory. Call
    ``resolve_canonical_stack_root`` when a caller specifically requires the
    authoritative ``PrivateMemoryStack`` root containing ``canonical.sqlite``.
    """

    values = dict(os.environ if env is None else env)
    explicit = values.get(PRIVATE_MEMORY_ROOT_ENV, "").strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        anchor_database = root / CANONICAL_DATABASE_NAME
        source = "explicit"
    else:
        if not values.get(PRIVATE_MEMORY_CONFIG_ENV, "").strip():
            raise PrivateMemoryRootResolutionError("private_memory_root_unavailable")
        try:
            connector = PrivateMemoryConnector(env=values)
            anchor_database = connector._load_db_path().expanduser().resolve()
        except Exception as exc:  # noqa: BLE001 - public caller uses bounded reason codes.
            raise PrivateMemoryRootResolutionError(
                "configured_private_memory_unavailable"
            ) from exc
        root = anchor_database.parent
        source = "private_config_parent"

    _validate_root(root, anchor_database, checkout=checkout)
    return str(root), source


def resolve_canonical_stack_root(
    env: Mapping[str, str] | None = None,
    *,
    checkout: str | Path | None = None,
    home: str | Path | None = None,
) -> tuple[str, str]:
    """Resolve one already-existing canonical ``PrivateMemoryStack`` root.

    This function never creates a directory or database and never treats a
    legacy connector SQLite file as the canonical stack. Resolution order is
    explicit Runner root, explicit stack root, canonical connector config, and
    the documented user-local ``PrivateMemoryStack`` default.
    """

    values = dict(os.environ if env is None else env)
    explicit_runner = values.get(PRIVATE_MEMORY_ROOT_ENV, "").strip()
    if explicit_runner:
        root = _existing_canonical_candidate(explicit_runner)
        if root is not None:
            _validate_root(root, root / CANONICAL_DATABASE_NAME, checkout=checkout)
            return str(root), "runner_explicit_canonical"

    explicit_stack = values.get(PRIVATE_MEMORY_STACK_ROOT_ENV, "").strip()
    if explicit_stack:
        root = _existing_canonical_candidate(explicit_stack)
        if root is None:
            raise PrivateMemoryRootResolutionError(
                "canonical_memory_anchor_unavailable"
            )
        _validate_root(root, root / CANONICAL_DATABASE_NAME, checkout=checkout)
        return str(root), "stack_explicit"

    config_value = values.get(PRIVATE_MEMORY_CONFIG_ENV, "").strip()
    if config_value:
        try:
            connector = PrivateMemoryConnector(env=values)
            configured_database = connector._load_db_path().expanduser().resolve()
        except Exception as exc:  # noqa: BLE001 - bounded reason at protected caller.
            raise PrivateMemoryRootResolutionError(
                "configured_private_memory_unavailable"
            ) from exc
        if configured_database.name == CANONICAL_DATABASE_NAME:
            root = configured_database.parent
            _validate_root(root, configured_database, checkout=checkout)
            return str(root), "canonical_private_config"

    home_root = Path(home).expanduser() if home is not None else Path.home()
    default_root = (home_root / DEFAULT_CANONICAL_STACK_RELATIVE).resolve()
    anchor = default_root / CANONICAL_DATABASE_NAME
    if anchor.is_file():
        _validate_root(default_root, anchor, checkout=checkout)
        return str(default_root), "documented_default"

    raise PrivateMemoryRootResolutionError("canonical_memory_root_unavailable")


def _existing_canonical_candidate(value: str | Path) -> Path | None:
    try:
        root = Path(value).expanduser().resolve(strict=True)
    except OSError:
        return None
    return root if (root / CANONICAL_DATABASE_NAME).is_file() else None


def _validate_root(
    root: Path,
    anchor_database: Path,
    *,
    checkout: str | Path | None,
) -> None:
    if not root.is_absolute() or not root.is_dir():
        raise PrivateMemoryRootResolutionError("private_memory_root_unavailable")
    if _path_has_symlink_component(root):
        raise PrivateMemoryRootResolutionError("private_memory_root_unsafe")
    if not anchor_database.is_file():
        raise PrivateMemoryRootResolutionError("private_memory_anchor_unavailable")
    if checkout is not None:
        checkout_path = Path(checkout).expanduser().resolve()
        if root == checkout_path or _is_relative_to(root, checkout_path):
            raise PrivateMemoryRootResolutionError("private_memory_root_inside_checkout")


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
