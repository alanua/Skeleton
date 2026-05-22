from __future__ import annotations

import pytest

from core.task_tree import (
    TaskNode,
    assert_acyclic_task_nodes,
    runnable_leaf_tasks,
    validate_task_nodes,
)


def test_validate_task_nodes_indexes_valid_nodes() -> None:
    nodes = [
        TaskNode(id="root", project_id="skeleton"),
        TaskNode(id="leaf", project_id="skeleton", parent_id="root"),
    ]

    task_map = validate_task_nodes(nodes)

    assert set(task_map) == {"root", "leaf"}
    assert task_map["leaf"].parent_id == "root"


def test_duplicate_task_ids_are_rejected() -> None:
    nodes = [
        TaskNode(id="task-1", project_id="skeleton"),
        TaskNode(id="task-1", project_id="skeleton"),
    ]

    with pytest.raises(ValueError, match="duplicate task id"):
        validate_task_nodes(nodes)


def test_unknown_parent_and_dependencies_are_rejected() -> None:
    with pytest.raises(ValueError, match="parent_id references unknown task"):
        validate_task_nodes(
            [TaskNode(id="child", project_id="skeleton", parent_id="missing")]
        )

    with pytest.raises(ValueError, match="dependency_ids reference unknown task"):
        validate_task_nodes(
            [TaskNode(id="child", project_id="skeleton", dependency_ids=("missing",))]
        )


def test_cross_project_edges_are_rejected() -> None:
    with pytest.raises(ValueError, match="parent must be in the same project"):
        validate_task_nodes(
            [
                TaskNode(id="parent", project_id="skeleton"),
                TaskNode(id="child", project_id="aufmass", parent_id="parent"),
            ]
        )

    with pytest.raises(ValueError, match="dependencies must be in the same project"):
        validate_task_nodes(
            [
                TaskNode(id="dependency", project_id="skeleton"),
                TaskNode(
                    id="child",
                    project_id="aufmass",
                    dependency_ids=("dependency",),
                ),
            ]
        )


def test_parent_cycles_are_rejected() -> None:
    nodes = [
        TaskNode(id="a", project_id="skeleton", parent_id="c"),
        TaskNode(id="b", project_id="skeleton", parent_id="a"),
        TaskNode(id="c", project_id="skeleton", parent_id="b"),
    ]

    with pytest.raises(ValueError, match="parent cycle detected"):
        assert_acyclic_task_nodes(nodes)


def test_dependency_cycles_are_rejected() -> None:
    nodes = [
        TaskNode(id="a", project_id="skeleton", dependency_ids=("c",)),
        TaskNode(id="b", project_id="skeleton", dependency_ids=("a",)),
        TaskNode(id="c", project_id="skeleton", dependency_ids=("b",)),
    ]

    with pytest.raises(ValueError, match="dependency cycle detected"):
        validate_task_nodes(nodes)


def test_runnable_leaf_tasks_excludes_parents_and_waiting_dependencies() -> None:
    nodes = [
        TaskNode(id="plan", project_id="skeleton", priority=99),
        TaskNode(id="setup", project_id="skeleton", parent_id="plan", status="done"),
        TaskNode(
            id="blocked",
            project_id="skeleton",
            parent_id="plan",
            dependency_ids=("review",),
            priority=10,
        ),
        TaskNode(id="review", project_id="skeleton", parent_id="plan"),
        TaskNode(id="other", project_id="aufmass", priority=20),
    ]

    assert [node.id for node in runnable_leaf_tasks(nodes)] == ["other", "review"]
    assert [node.id for node in runnable_leaf_tasks(nodes, project_id="skeleton")] == [
        "review"
    ]


def test_runnable_leaf_tasks_returns_done_dependency_dependents_by_priority() -> None:
    nodes = [
        TaskNode(id="root", project_id="skeleton"),
        TaskNode(id="prepare", project_id="skeleton", parent_id="root", status="done"),
        TaskNode(
            id="implement",
            project_id="skeleton",
            parent_id="root",
            dependency_ids=("prepare",),
            priority=5,
        ),
        TaskNode(id="docs", project_id="skeleton", parent_id="root", priority=5),
        TaskNode(id="cleanup", project_id="skeleton", parent_id="root", priority=1),
    ]

    assert [node.id for node in runnable_leaf_tasks(nodes)] == [
        "docs",
        "implement",
        "cleanup",
    ]
