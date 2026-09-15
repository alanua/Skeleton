from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.private_memory_root_resolver import (
    PRIVATE_MEMORY_ROOT_ENV,
    PRIVATE_MEMORY_STACK_ROOT_ENV,
    PrivateMemoryRootResolutionError,
    resolve_canonical_stack_root,
    resolve_private_memory_root,
)


def _canonical_root(tmp_path: Path, name: str = "private") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True)
    (root / "canonical.sqlite").write_bytes(b"sqlite-placeholder")
    return root


def _config(tmp_path: Path, database: Path) -> Path:
    config = tmp_path / f"{database.stem}-private-memory.json"
    config.write_text(
        json.dumps(
            {
                "schema": "skeleton.private_memory.config.v0",
                "database": {"path": str(database)},
            }
        ),
        encoding="utf-8",
    )
    return config


def test_resolves_explicit_existing_stack_root(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path)
    resolved, source = resolve_private_memory_root(
        {PRIVATE_MEMORY_ROOT_ENV: str(root)}, checkout=tmp_path / "checkout"
    )
    assert resolved == str(root.resolve())
    assert source == "explicit"


def test_resolves_existing_canonical_config_without_duplicate_env(
    tmp_path: Path,
) -> None:
    root = _canonical_root(tmp_path)
    resolved, source = resolve_private_memory_root(
        {
            "SKELETON_PRIVATE_MEMORY_CONFIG": str(
                _config(tmp_path, root / "canonical.sqlite")
            )
        },
        checkout=tmp_path / "checkout",
    )
    assert resolved == str(root.resolve())
    assert source == "private_config_parent"


def test_legacy_connector_database_only_anchors_private_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private"
    root.mkdir()
    legacy_database = root / "legacy-heartbeat.sqlite"
    legacy_database.write_bytes(b"sqlite-placeholder")
    resolved, source = resolve_private_memory_root(
        {
            "SKELETON_PRIVATE_MEMORY_CONFIG": str(
                _config(tmp_path, legacy_database)
            )
        },
        checkout=tmp_path / "checkout",
    )
    assert resolved == str(root.resolve())
    assert source == "private_config_parent"
    assert not (root / "canonical.sqlite").exists()


def test_memory_resolver_prefers_existing_documented_canonical_default(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy_database = legacy_root / "embodied-memory.sqlite3"
    legacy_database.write_bytes(b"sqlite-placeholder")
    home = tmp_path / "home"
    default_root = home / ".local" / "share" / "skeleton-private-memory"
    default_root.mkdir(parents=True)
    (default_root / "canonical.sqlite").write_bytes(b"sqlite-placeholder")
    resolved, source = resolve_private_memory_root(
        {
            "HOME": str(home),
            "SKELETON_PRIVATE_MEMORY_CONFIG": str(
                _config(tmp_path, legacy_database)
            ),
        },
        checkout=tmp_path / "checkout",
    )
    assert resolved == str(default_root.resolve())
    assert source == "documented_default_canonical"
    assert not (legacy_root / "canonical.sqlite").exists()


def test_canonical_resolver_prefers_explicit_runner_stack(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path, "runner-stack")
    resolved, source = resolve_canonical_stack_root(
        {PRIVATE_MEMORY_ROOT_ENV: str(root)},
        checkout=tmp_path / "checkout",
        home=tmp_path / "home",
    )
    assert resolved == str(root.resolve())
    assert source == "runner_explicit_canonical"


def test_canonical_resolver_uses_explicit_stack_env(tmp_path: Path) -> None:
    root = _canonical_root(tmp_path, "stack-env")
    resolved, source = resolve_canonical_stack_root(
        {PRIVATE_MEMORY_STACK_ROOT_ENV: str(root)},
        checkout=tmp_path / "checkout",
        home=tmp_path / "home",
    )
    assert resolved == str(root.resolve())
    assert source == "stack_explicit"


def test_canonical_resolver_accepts_only_canonical_config_database(
    tmp_path: Path,
) -> None:
    root = _canonical_root(tmp_path, "config-stack")
    resolved, source = resolve_canonical_stack_root(
        {
            "SKELETON_PRIVATE_MEMORY_CONFIG": str(
                _config(tmp_path, root / "canonical.sqlite")
            )
        },
        checkout=tmp_path / "checkout",
        home=tmp_path / "home",
    )
    assert resolved == str(root.resolve())
    assert source == "canonical_private_config"


def test_canonical_resolver_ignores_legacy_config_and_uses_documented_default(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy_database = legacy_root / "embodied-memory.sqlite3"
    legacy_database.write_bytes(b"sqlite-placeholder")
    home = tmp_path / "home"
    default_root = home / ".local" / "share" / "skeleton-private-memory"
    default_root.mkdir(parents=True)
    (default_root / "canonical.sqlite").write_bytes(b"sqlite-placeholder")
    resolved, source = resolve_canonical_stack_root(
        {
            "SKELETON_PRIVATE_MEMORY_CONFIG": str(
                _config(tmp_path, legacy_database)
            )
        },
        checkout=tmp_path / "checkout",
        home=home,
    )
    assert resolved == str(default_root.resolve())
    assert source == "documented_default"
    assert not (legacy_root / "canonical.sqlite").exists()


def test_canonical_resolver_never_creates_missing_default(tmp_path: Path) -> None:
    home = tmp_path / "home"
    with pytest.raises(PrivateMemoryRootResolutionError) as exc_info:
        resolve_canonical_stack_root(
            {}, checkout=tmp_path / "checkout", home=home
        )
    assert exc_info.value.reason_code == "canonical_memory_root_unavailable"
    assert not (home / ".local" / "share" / "skeleton-private-memory").exists()


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
    assert exc_info.value.reason_code == "private_memory_root_unavailable"
