from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.private_memory_root_resolver import (
    PRIVATE_MEMORY_ROOT_ENV,
    PrivateMemoryRootResolutionError,
    resolve_private_memory_root,
)


def _canonical_root(tmp_path: Path, name: str = "private") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "canonical.sqlite").write_bytes(b"sqlite-placeholder")
    return root


def test_resolves_explicit_existing_stack_root(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path)

    resolved, source = resolve_private_memory_root(
        {PRIVATE_MEMORY_ROOT_ENV: str(root)}, checkout=tmp_path / "checkout"
    )

    assert resolved == str(root.resolve())
    assert source == "explicit"


def test_resolves_existing_canonical_config_without_duplicate_env(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path)
    config = tmp_path / "private-memory.json"
    config.write_text(
        json.dumps(
            {
                "schema": "skeleton.private_memory.config.v0",
                "database": {"path": str(root / "canonical.sqlite")},
            }
        ),
        encoding="utf-8",
    )

    resolved, source = resolve_private_memory_root(
        {"SKELETON_PRIVATE_MEMORY_CONFIG": str(config)},
        checkout=tmp_path / "checkout",
    )

    assert resolved == str(root.resolve())
    assert source == "canonical_config"


def test_rejects_configured_database_that_is_not_stack_canonical(tmp_path: Path) -> None:
    root = tmp_path / "private"
    root.mkdir()
    database = root / "other.sqlite"
    database.write_bytes(b"sqlite-placeholder")
    config = tmp_path / "private-memory.json"
    config.write_text(
        json.dumps(
            {
                "schema": "skeleton.private_memory.config.v0",
                "database": {"path": str(database)},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PrivateMemoryRootResolutionError) as exc_info:
        resolve_private_memory_root(
            {"SKELETON_PRIVATE_MEMORY_CONFIG": str(config)},
            checkout=tmp_path / "checkout",
        )

    assert exc_info.value.reason_code == "configured_private_memory_not_stack_canonical"


def test_rejects_private_root_inside_public_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    root = checkout / "private"
    root.mkdir(parents=True)
    (root / "canonical.sqlite").write_bytes(b"sqlite-placeholder")

    with pytest.raises(PrivateMemoryRootResolutionError) as exc_info:
        resolve_private_memory_root(
            {PRIVATE_MEMORY_ROOT_ENV: str(root)}, checkout=checkout
        )

    assert exc_info.value.reason_code == "private_memory_root_inside_checkout"


def test_missing_config_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(PrivateMemoryRootResolutionError) as exc_info:
        resolve_private_memory_root({}, checkout=tmp_path / "checkout")

    assert exc_info.value.reason_code == "configured_private_memory_unavailable"
