from __future__ import annotations

# Exact-path regression: live configured DB is reused, synthetic roots remain isolated.

import json
from pathlib import Path

from core.private_memory_stack import PrivateMemoryStack


def _config(tmp_path: Path, database: Path) -> Path:
    path = tmp_path / "private-memory.json"
    path.write_text(
        json.dumps(
            {
                "schema": "skeleton.private_memory.config.v0",
                "database": {"path": str(database)},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_stack_uses_exact_configured_database_when_root_matches(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "private"
    root.mkdir()
    database = root / "legacy-private-memory.sqlite"
    database.write_bytes(b"sqlite-placeholder")
    config = _config(tmp_path, database)
    monkeypatch.setenv("SKELETON_PRIVATE_MEMORY_CONFIG", str(config))

    stack = PrivateMemoryStack(root)

    assert stack.paths.root == root.resolve()
    assert stack.paths.db == database.resolve()


def test_synthetic_child_root_does_not_reuse_live_configured_database(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "private"
    root.mkdir()
    database = root / "legacy-private-memory.sqlite"
    database.write_bytes(b"sqlite-placeholder")
    config = _config(tmp_path, database)
    monkeypatch.setenv("SKELETON_PRIVATE_MEMORY_CONFIG", str(config))
    smoke_root = root / "activation_smoke" / "run"

    stack = PrivateMemoryStack(smoke_root)

    assert stack.paths.root == smoke_root.resolve()
    assert stack.paths.db == smoke_root.resolve() / "canonical.sqlite"


def test_stack_without_config_keeps_canonical_filename(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SKELETON_PRIVATE_MEMORY_CONFIG", raising=False)
    root = tmp_path / "private"

    stack = PrivateMemoryStack(root)

    assert stack.paths.db == root.resolve() / "canonical.sqlite"
