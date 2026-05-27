from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping


OPEN_STATUSES = frozenset({"pending", "queued", "ready"})
DONE_STATUSES = frozenset({"done", "completed"})
TERMINAL_STATUSES = DONE_STATUSES | frozenset({"cancelled", "skipped"})


@dataclass(frozen=True)
class TaskNode:
    id: str
    project_id: str
    parent_id: str | None = None
    dependency_ids: tuple[str, ...] = field(default_factory=tuple)
    status: str = "pending"
    priority: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or self.id.strip() == "":
            raise ValueError("task id must be a non-empty string.")
        if not isinstance(self.project_id, str) or self.project_id.strip() == "":
            raise ValueError("project_id must be a non-empty string.")
        if self.parent_id is not None and (
            not isinstance(self.parent_id, str) or self.parent_id.strip() == ""
        ):
            raise ValueError("parent_id must be None or a non-empty string.")
        if not isinstance(self.status, str) or self.status.strip() == "":
            raise ValueError("status must be a non-empty string.")
        if not isinstance(self.priority, int):
            raise ValueError("priority must be an integer.")

        normalized_dependencies = tuple(self.dependency_ids)
        for dependency_id in normalized_dependencies:
            if not isinstance(dependency_id, str) or dependency_id.strip() == "":
                raise ValueError("dependency_ids must contain non-empty strings.")
        object.__setattr__(self, "dependency_ids", normalized_dependencies)


def task_index(tasks: Iterable[TaskNode]) -> dict[str, TaskNode]:
    index: dict[str, TaskNode] = {}
    for task in tasks:
        if not isinstance(task, TaskNode):
            raise ValueError("tasks must contain TaskNode instances.")
        if task.id in index:
            raise ValueError(f"duplicate task id {task.id!r}.")
        index[task.id] = task
    return index


def validate_task_tree(tasks: Iterable[TaskNode]) -> dict[str, TaskNode]:
    index = task_index(tasks)
    for task in index.values():
        if task.parent_id is not None:
            parent = _require_known_task(index, task.parent_id, "parent")
            if parent.project_id != task.project_id:
                raise ValueError(f"task {task.id!r} parent belongs to a different project.")

        for dependency_id in task.dependency_ids:
            dependency = _require_known_task(index, dependency_id, "dependency")
            if dependency.project_id != task.project_id:
                raise ValueError(
                    f"task {task.id!r} dependency {dependency_id!r} belongs to a different project."
                )

    find_task_cycles(index)
    return index


def find_task_cycles(tasks: Iterable[TaskNode] | Mapping[str, TaskNode]) -> list[tuple[str, ...]]:
    index = _coerce_index(tasks)
    cycles: list[tuple[str, ...]] = []
    _collect_cycles(index, lambda task: [task.parent_id] if task.parent_id is not None else [], cycles)
    _collect_cycles(index, lambda task: list(task.dependency_ids), cycles)
    return cycles


def assert_acyclic(tasks: Iterable[TaskNode] | Mapping[str, TaskNode]) -> None:
    cycles = find_task_cycles(tasks)
    if cycles:
        cycle = " -> ".join(cycles[0])
        raise ValueError(f"task tree contains a cycle: {cycle}.")


def runnable_leaf_tasks(
    tasks: Iterable[TaskNode] | Mapping[str, TaskNode],
    *,
    project_id: str | None = None,
) -> list[TaskNode]:
    index = validate_task_tree(_coerce_index(tasks).values())
    children_by_parent = _children_by_parent(index.values())

    runnable = [
        task
        for task in index.values()
        if (project_id is None or task.project_id == project_id)
        and task.status in OPEN_STATUSES
        and task.id not in children_by_parent
        and all(index[dependency_id].status in DONE_STATUSES for dependency_id in task.dependency_ids)
    ]
    return sorted(runnable, key=lambda task: (-task.priority, task.id))


def blocked_leaf_tasks(
    tasks: Iterable[TaskNode] | Mapping[str, TaskNode],
    *,
    project_id: str | None = None,
) -> list[TaskNode]:
    index = validate_task_tree(_coerce_index(tasks).values())
    children_by_parent = _children_by_parent(index.values())

    blocked = [
        task
        for task in index.values()
        if (project_id is None or task.project_id == project_id)
        and task.status in OPEN_STATUSES
        and task.id not in children_by_parent
        and any(index[dependency_id].status not in DONE_STATUSES for dependency_id in task.dependency_ids)
    ]
    return sorted(blocked, key=lambda task: (-task.priority, task.id))


def _coerce_index(tasks: Iterable[TaskNode] | Mapping[str, TaskNode]) -> dict[str, TaskNode]:
    if isinstance(tasks, Mapping):
        for task_id, task in tasks.items():
            if task.id != task_id:
                raise ValueError(f"task index key {task_id!r} does not match task id {task.id!r}.")
        return dict(tasks)
    return task_index(tasks)


def _children_by_parent(tasks: Iterable[TaskNode]) -> dict[str, list[TaskNode]]:
    children: dict[str, list[TaskNode]] = {}
    for task in tasks:
        if task.parent_id is not None:
            children.setdefault(task.parent_id, []).append(task)
    return children


def _collect_cycles(
    index: Mapping[str, TaskNode],
    next_ids_for: Callable[[TaskNode], list[str]],
    cycles: list[tuple[str, ...]],
) -> None:
    visited: set[str] = set()
    path: list[str] = []
    active: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in active:
            cycle_start = path.index(task_id)
            cycles.append(tuple(path[cycle_start:] + [task_id]))
            return
        if task_id in visited:
            return

        visited.add(task_id)
        active.add(task_id)
        path.append(task_id)
        for next_id in next_ids_for(index[task_id]):
            if next_id in index:
                visit(next_id)
        path.pop()
        active.remove(task_id)

    for task_id in index:
        visit(task_id)


def _require_known_task(
    index: Mapping[str, TaskNode],
    task_id: str,
    relationship: str,
) -> TaskNode:
    try:
        return index[task_id]
    except KeyError as exc:
        raise ValueError(f"unknown {relationship} task id {task_id!r}.") from exc
