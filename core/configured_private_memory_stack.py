from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from core.private_memory import (
    PRIVATE_MEMORY_CONFIG_ENV,
    CanonicalPrivateMemoryStore,
    PrivateMemoryConnector,
)
from core.private_memory_stack import (
    PrivateMemoryStack,
    PrivateMemoryStackError,
    _paths,
)


class ConfiguredPrivateMemoryStack(PrivateMemoryStack):
    """Private stack that reuses the configured authority only for its exact root."""

    def __init__(
        self,
        private_root: str | Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        values = dict(os.environ if env is None else env)
        paths = _paths(private_root)
        config_path = values.get(PRIVATE_MEMORY_CONFIG_ENV, "").strip()
        if config_path:
            try:
                configured_db = (
                    PrivateMemoryConnector(env=values)
                    ._load_db_path()
                    .expanduser()
                    .resolve()
                )
            except Exception as exc:  # noqa: BLE001 - fail closed with bounded class.
                raise PrivateMemoryStackError(
                    "configured authoritative database is unavailable"
                ) from exc
            if configured_db.parent == paths.root:
                if not configured_db.is_file():
                    raise PrivateMemoryStackError(
                        "configured authoritative database is unavailable"
                    )
                paths = replace(paths, db=configured_db)
        self.paths = paths
        self.store = CanonicalPrivateMemoryStore(paths.db)
