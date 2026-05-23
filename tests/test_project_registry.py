from pathlib import Path

import pytest

from core.project_registry import (
    load_project_registry,
    remote_matches_repository,
    resolve_registry_target,
    validate_registry_entry_ready,
    validate_project_registry,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT_REGISTRY_PATH = ROOT / "PROJECT_REGISTRY.yaml"


def test_current_registry_entries_resolve() -> None:
    registry = load_project_registry(PROJECT_REGISTRY_PATH)

    assert registry["default_project"] == "skeleton"
    assert resolve_registry_target(registry, target_project="skeleton").repository == (
        "alanua/Skeleton"
    )
    assert resolve_registry_target(
        registry, target_repository="alanua/bauclock"
    ).project_id == "bauclock"
    assert resolve_registry_target(
        registry, target_repository="alanua/Lavalamp"
    ).project_id == "lavalamp"


def test_future_registry_entry_resolves_without_code_change(tmp_path: Path) -> None:
    registry = {
        "schema": "skeleton.project_registry.v1",
        "default_project": "skeleton",
        "projects": {
            "skeleton": {
                "project_id": "skeleton",
                "repository": "alanua/Skeleton",
                "checkout_path": str(tmp_path / "skeleton"),
                "worktree_root": str(tmp_path / "worktrees" / "skeleton"),
                "base_branch": "main",
                "runner_modes": ["codex_issue_worktree"],
                "enabled": True,
            },
            "future": {
                "project_id": "future",
                "repository": "alanua/future",
                "checkout_path": str(tmp_path / "future"),
                "worktree_root": str(tmp_path / "worktrees" / "future"),
                "base_branch": "main",
                "runner_modes": ["planning_only"],
                "enabled": True,
            },
        },
    }

    entry = resolve_registry_target(
        validate_project_registry(registry),
        target_project="future",
    )

    assert entry.project_id == "future"
    assert entry.repository == "alanua/future"


def test_unknown_registry_entry_is_rejected() -> None:
    registry = load_project_registry(PROJECT_REGISTRY_PATH)

    with pytest.raises(KeyError, match="unknown project_id"):
        resolve_registry_target(registry, target_project="missing")


def test_disabled_registry_entry_is_not_ready(tmp_path: Path) -> None:
    checkout = tmp_path / "disabled"
    checkout.mkdir()
    registry = {
        "schema": "skeleton.project_registry.v1",
        "default_project": "disabled",
        "projects": {
            "disabled": {
                "project_id": "disabled",
                "repository": "alanua/disabled",
                "checkout_path": str(checkout),
                "worktree_root": str(tmp_path / "worktrees" / "disabled"),
                "base_branch": "main",
                "runner_modes": ["planning_only"],
                "enabled": False,
            }
        },
    }
    entry = resolve_registry_target(validate_project_registry(registry))

    with pytest.raises(ValueError, match="disabled"):
        validate_registry_entry_ready(entry, remote_reader=lambda _path: "alanua/disabled")


def test_unsafe_registry_paths_are_rejected() -> None:
    registry = {
        "schema": "skeleton.project_registry.v1",
        "default_project": "unsafe",
        "projects": {
            "unsafe": {
                "project_id": "unsafe",
                "repository": "alanua/unsafe",
                "checkout_path": "../unsafe",
                "worktree_root": "/tmp/worktrees/unsafe",
                "base_branch": "main",
                "runner_modes": ["planning_only"],
                "enabled": True,
            }
        },
    }

    with pytest.raises(ValueError, match="checkout_path must be absolute"):
        validate_project_registry(registry)


def test_missing_checkout_and_wrong_remote_are_not_ready(tmp_path: Path) -> None:
    registry = {
        "schema": "skeleton.project_registry.v1",
        "default_project": "project",
        "projects": {
            "project": {
                "project_id": "project",
                "repository": "alanua/project",
                "checkout_path": str(tmp_path / "project"),
                "worktree_root": str(tmp_path / "worktrees" / "project"),
                "base_branch": "main",
                "runner_modes": ["planning_only"],
                "enabled": True,
            }
        },
    }
    entry = resolve_registry_target(validate_project_registry(registry))

    with pytest.raises(ValueError, match="checkout_path does not exist"):
        validate_registry_entry_ready(entry, remote_reader=lambda _path: "alanua/project")

    entry.checkout_path.mkdir()
    with pytest.raises(ValueError, match="checkout remote does not match"):
        validate_registry_entry_ready(entry, remote_reader=lambda _path: "alanua/wrong")


def test_remote_match_accepts_github_forms() -> None:
    assert remote_matches_repository("alanua/Skeleton", "alanua/Skeleton")
    assert remote_matches_repository(
        "https://github.com/alanua/Skeleton.git",
        "alanua/Skeleton",
    )
    assert remote_matches_repository(
        "git@github.com:alanua/Skeleton.git",
        "alanua/Skeleton",
    )
