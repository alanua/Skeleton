from pathlib import Path
from unittest import mock

import pytest

import core.project_tree as project_tree
from core.project_tree import (
    get_project_by_repo,
    get_project,
    load_project_tree,
    plan_worktree_name,
    validate_project_tree,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT_TREE_PATH = ROOT / "PROJECT_TREE.yaml"


def loaded_tree() -> dict:
    return load_project_tree(PROJECT_TREE_PATH)


def test_required_projects_are_present() -> None:
    tree = loaded_tree()

    assert tree["version"] == "1.0.0"
    assert tree["default_project"] == "skeleton"
    assert {
        "skeleton",
        "bauclock",
        "lavalamp",
        "aufmass_private",
        "home_automation",
    } <= set(tree["projects"])


def test_registry_has_real_runner_paths() -> None:
    tree = loaded_tree()

    assert get_project(tree, "skeleton")["checkout_path"] == (
        "/home/agent/agent-dev/repos/Skeleton"
    )
    assert get_project(tree, "skeleton")["worktree_root"] == (
        "/home/agent/agent-dev/worktrees/skeleton"
    )
    assert get_project(tree, "bauclock")["checkout_path"] == (
        "/home/agent/agent-dev/worktrees/bauclock/main"
    )
    assert get_project(tree, "bauclock")["worktree_root"] == (
        "/home/agent/agent-dev/worktrees/bauclock"
    )
    assert get_project(tree, "lavalamp")["checkout_path"] == (
        "/home/agent/agent-dev/worktrees/lavalamp/main"
    )
    assert get_project(tree, "lavalamp")["worktree_root"] == (
        "/home/agent/agent-dev/worktrees/lavalamp"
    )


def test_current_projects_resolve_through_registry() -> None:
    tree = loaded_tree()

    assert get_project_by_repo(tree, "alanua/Skeleton")["worktree_name_prefix"] == "skeleton"
    assert get_project_by_repo(tree, "alanua/bauclock")["worktree_name_prefix"] == "bauclock"
    assert get_project_by_repo(tree, "alanua/Lavalamp")["worktree_name_prefix"] == "lavalamp"


def test_future_registry_entry_resolves_without_code_changes() -> None:
    tree = loaded_tree()
    tree["projects"]["future_app"] = {
        "repo": "alanua/future-app",
        "checkout_path": "/home/agent/agent-dev/worktrees/future-app/main",
        "worktree_root": "/home/agent/agent-dev/worktrees/future-app",
        "public": True,
        "future_parallel_worktrees": True,
        "runtime_approval_required": True,
        "worktree_name_prefix": "future-app",
    }

    assert get_project_by_repo(tree, "alanua/future-app")["worktree_root"] == (
        "/home/agent/agent-dev/worktrees/future-app"
    )


def test_registry_paths_outside_approved_workspace_are_rejected() -> None:
    tree = loaded_tree()
    tree["projects"]["skeleton"]["checkout_path"] = "/tmp/Skeleton"

    with pytest.raises(ValueError, match="checkout_path must stay under"):
        validate_project_tree(tree)


def test_registry_workspace_root_override_is_supported_for_tests(tmp_path: Path) -> None:
    tree = loaded_tree()
    root = tmp_path / "agent-dev"
    for project_id, project in tree["projects"].items():
        project["checkout_path"] = str(root / "checkouts" / project_id / "main")
        project["worktree_root"] = str(root / "worktrees" / project_id)

    with mock.patch.dict(
        "os.environ",
        {project_tree.APPROVED_WORKSPACE_ROOT_ENV: str(root)},
        clear=True,
    ):
        assert validate_project_tree(tree)["projects"]["skeleton"]["checkout_path"] == (
            str(root / "checkouts" / "skeleton" / "main")
        )


def test_checkout_path_must_not_equal_worktree_root() -> None:
    tree = loaded_tree()
    tree["projects"]["skeleton"]["checkout_path"] = tree["projects"]["skeleton"][
        "worktree_root"
    ]

    with pytest.raises(ValueError, match="checkout_path must not equal worktree_root"):
        validate_project_tree(tree)


def test_skeleton_project_allows_future_parallel_work() -> None:
    skeleton = get_project(loaded_tree(), "skeleton")

    assert skeleton["future_parallel_worktrees"] is True


def test_private_projects_are_marked_non_public() -> None:
    tree = loaded_tree()

    assert get_project(tree, "aufmass_private")["public"] is False
    assert get_project(tree, "home_automation")["public"] is False


def test_home_automation_project_requires_runtime_approval() -> None:
    home_automation = get_project(loaded_tree(), "home_automation")

    assert home_automation["runtime_approval_required"] is True


def test_worktree_names_are_deterministic() -> None:
    name = plan_worktree_name("skeleton", "issue-137 ProjectTree stage 1")

    assert name == plan_worktree_name("skeleton", "issue-137 ProjectTree stage 1")
    assert name.startswith("skeleton-issue-137-projecttree-stage-1-")


def test_path_traversal_project_ids_are_rejected() -> None:
    tree = loaded_tree()
    tree["projects"]["../escape"] = tree["projects"]["skeleton"]

    with pytest.raises(ValueError, match="invalid project_id"):
        validate_project_tree(tree)

    with pytest.raises(ValueError, match="invalid project_id"):
        get_project(loaded_tree(), "../escape")

    with pytest.raises(ValueError, match="invalid project_id"):
        plan_worktree_name("../escape", "issue-137")


def test_project_tree_has_no_subprocess_usage() -> None:
    source = Path(project_tree.__file__).read_text(encoding="utf-8")

    assert "subprocess" not in source
