import pytest

from core.task_tree import (
    TaskNode,
    assert_acyclic,
    blocked_leaf_tasks,
    find_task_cycles,
    runnable_leaf_tasks,
    validate_task_tree,
)


def test_task_node_normalizes_dependency_ids() -> None:
    task = TaskNode(
        id="implement",
        project_id="skeleton",
        dependency_ids=["design", "review"],
    )

    assert task.dependency_ids == ("design", "review")


def test_validate_task_tree_rejects_duplicate_ids() -> None:
    tasks = [
        TaskNode(id="same", project_id="skeleton"),
        TaskNode(id="same", project_id="skeleton"),
    ]

    with pytest.raises(ValueError, match="duplicate task id"):
        validate_task_tree(tasks)


def test_validate_task_tree_rejects_unknown_parent() -> None:
    tasks = [TaskNode(id="child", project_id="skeleton", parent_id="missing")]

    with pytest.raises(ValueError, match="unknown parent task id"):
        validate_task_tree(tasks)


def test_validate_task_tree_rejects_cross_project_parent() -> None:
    tasks = [
        TaskNode(id="parent", project_id="skeleton"),
        TaskNode(id="child", project_id="aufmass", parent_id="parent"),
    ]

    with pytest.raises(ValueError, match="parent belongs to a different project"):
        validate_task_tree(tasks)


def test_validate_task_tree_rejects_unknown_dependency() -> None:
    tasks = [TaskNode(id="child", project_id="skeleton", dependency_ids=("missing",))]

    with pytest.raises(ValueError, match="unknown dependency task id"):
        validate_task_tree(tasks)


def test_validate_task_tree_rejects_cross_project_dependency() -> None:
    tasks = [
        TaskNode(id="setup", project_id="aufmass", status="done"),
        TaskNode(id="child", project_id="skeleton", dependency_ids=("setup",)),
    ]

    with pytest.raises(ValueError, match="dependency 'setup' belongs to a different project"):
        validate_task_tree(tasks)


def test_find_task_cycles_detects_parent_cycles() -> None:
    tasks = [
        TaskNode(id="a", project_id="skeleton", parent_id="b"),
        TaskNode(id="b", project_id="skeleton", parent_id="a"),
    ]

    assert ("a", "b", "a") in find_task_cycles(tasks)


def test_assert_acyclic_rejects_dependency_cycles() -> None:
    tasks = [
        TaskNode(id="a", project_id="skeleton", dependency_ids=("b",)),
        TaskNode(id="b", project_id="skeleton", dependency_ids=("a",)),
    ]

    with pytest.raises(ValueError, match="task tree contains a cycle"):
        assert_acyclic(tasks)


def test_runnable_leaf_tasks_excludes_non_leaf_parent_tasks() -> None:
    tasks = [
        TaskNode(id="parent", project_id="skeleton", priority=100),
        TaskNode(id="child", project_id="skeleton", parent_id="parent"),
    ]

    assert runnable_leaf_tasks(tasks) == [tasks[1]]


def test_runnable_leaf_tasks_requires_completed_dependencies() -> None:
    tasks = [
        TaskNode(id="setup", project_id="skeleton", status="running"),
        TaskNode(id="blocked", project_id="skeleton", dependency_ids=("setup",)),
    ]

    assert runnable_leaf_tasks(tasks) == []
    assert blocked_leaf_tasks(tasks) == [tasks[1]]


def test_runnable_leaf_tasks_orders_by_priority_then_id() -> None:
    tasks = [
        TaskNode(id="middle", project_id="skeleton", priority=5),
        TaskNode(id="late", project_id="skeleton", priority=1),
        TaskNode(id="alpha", project_id="skeleton", priority=5),
        TaskNode(id="done", project_id="skeleton", status="done", priority=100),
    ]

    assert [task.id for task in runnable_leaf_tasks(tasks)] == ["alpha", "middle", "late"]


def test_runnable_leaf_tasks_can_filter_by_project() -> None:
    tasks = [
        TaskNode(id="skeleton-task", project_id="skeleton"),
        TaskNode(id="aufmass-task", project_id="aufmass"),
    ]

    assert runnable_leaf_tasks(tasks, project_id="aufmass") == [tasks[1]]
