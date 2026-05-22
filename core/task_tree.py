from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable, Mapping


TASK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$")
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

PENDING_STATUS = "pending"
DONE_STATUS = "done"
RUNNABLE_STATUSES = frozenset((PENDING_STATUS,))
DEPENDENCY_DONE_STATUSES = frozenset((DONE_STATUS,))


@dataclass(frozen=True)
class TaskNode:
    id: str
    project_id: str
    parent_id: str | None = None
    dependency_ids: tuple[str, ...] = field(default_factory=tuple)
    status: str = PENDING_STATUS
    priority: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependency_ids", tuple(self.dependency_ids))


def validate_task_nodes(nodes: Iterable[TaskNode]) -> dict[str, TaskNode]:
    task_map: dict[str, TaskNode] = {}
    for node in nodes:
        _validate_node_shape(node)
        if node.id in task_map:
            raise ValueError(f"duplicate task id {node.id!r}.")
        task_map[node.id] = node

    for node in task_map.values():
        if node.parent_id is not None:
            if node.parent_id == node.id:
                raise ValueError(f"task {node.id!r} cannot be its own parent.")
            parent = task_map.get(node.parent_id)
            if parent is None:
                raise ValueError(f"task {node.id!r} parent_id references unknown task.")
            if parent.project_id != node.project_id:
                raise ValueError(f"task {node.id!r} parent must be in the same project.")

        for dependency_id in node.dependency_ids:
            if dependency_id == node.id:
                raise ValueError(f"task {node.id!r} cannot depend on itself.")
            dependency = task_map.get(dependency_id)
            if dependency is None:
                raise ValueError(
                    f"task {node.id!r} dependency_ids reference unknown task."
                )
            if dependency.project_id != node.project_id:
                raise ValueError(
                    f"task {node.id!r} dependencies must be in the same project."
                )

    _raise_on_cycle(task_map, "parent", _parent_edges(task_map))
    _raise_on_cycle(task_map, "dependency", _dependency_edges(task_map))
    return task_map


def assert_acyclic_task_nodes(nodes: Iterable[TaskNode]) -> None:
    validate_task_nodes(nodes)


def runnable_leaf_tasks(
    nodes: Iterable[TaskNode], project_id: str | None = None
) -> list[TaskNode]:
    task_map = validate_task_nodes(nodes)
    if project_id is not None:
        _validate_project_id(project_id)

    child_parent_ids = {
        node.parent_id for node in task_map.values() if node.parent_id is not None
    }
    runnable = [
        node
        for node in task_map.values()
        if node.id not in child_parent_ids
        and node.status in RUNNABLE_STATUSES
        and (project_id is None or node.project_id == project_id)
        and _dependencies_done(node, task_map)
    ]
    return sorted(runnable, key=lambda node: (-node.priority, node.id))


def _validate_node_shape(node: TaskNode) -> None:
    if not isinstance(node, TaskNode):
        raise ValueError("task nodes must be TaskNode instances.")
    _validate_task_id(node.id)
    _validate_project_id(node.project_id)
    if node.parent_id is not None:
        _validate_task_id(node.parent_id)
    for dependency_id in node.dependency_ids:
        _validate_task_id(dependency_id)
    if not isinstance(node.status, str) or node.status.strip() == "":
        raise ValueError(f"task {node.id!r} status must be a non-empty string.")
    if not isinstance(node.priority, int):
        raise ValueError(f"task {node.id!r} priority must be an integer.")


def _validate_task_id(task_id: object) -> None:
    if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError(f"invalid task id {task_id!r}.")


def _validate_project_id(project_id: object) -> None:
    if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError(f"invalid project_id {project_id!r}.")


def _dependencies_done(node: TaskNode, task_map: Mapping[str, TaskNode]) -> bool:
    return all(
        task_map[dependency_id].status in DEPENDENCY_DONE_STATUSES
        for dependency_id in node.dependency_ids
    )


def _parent_edges(task_map: Mapping[str, TaskNode]) -> dict[str, tuple[str, ...]]:
    return {
        node.id: (node.parent_id,) if node.parent_id is not None else ()
        for node in task_map.values()
    }


def _dependency_edges(task_map: Mapping[str, TaskNode]) -> dict[str, tuple[str, ...]]:
    return {node.id: node.dependency_ids for node in task_map.values()}


def _raise_on_cycle(
    task_map: Mapping[str, TaskNode],
    graph_name: str,
    edges: Mapping[str, tuple[str, ...]],
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            cycle_start = path.index(task_id)
            cycle = path[cycle_start:] + [task_id]
            raise ValueError(f"{graph_name} cycle detected: {' -> '.join(cycle)}.")

        visiting.add(task_id)
        path.append(task_id)
        for next_id in edges[task_id]:
            visit(next_id)
        path.pop()
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in task_map:
        visit(task_id)
