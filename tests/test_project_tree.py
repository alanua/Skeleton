from pathlib import Path

import pytest

import core.project_tree as project_tree
from core.project_tree import (
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
    assert {"skeleton", "aufmass_private", "home_automation"} <= set(tree["projects"])


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
