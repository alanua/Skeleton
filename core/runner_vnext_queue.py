from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from core.project_tree import get_project_by_repo, validate_project_tree


RunCommand = Callable[[Sequence[str]], tuple[int, str]]


@dataclass(frozen=True)
class RunnerVNextQueueSource:
    project_id: str
    repository: str


@dataclass(frozen=True)
class RunnerVNextQueueItem:
    source: RunnerVNextQueueSource
    issue: dict[str, Any]

    @property
    def source_repository(self) -> str:
        return self.source.repository

    @property
    def issue_number(self) -> int:
        return int(self.issue["number"])

    @property
    def identity(self) -> tuple[str, int]:
        return self.source_repository, self.issue_number


def registered_runner_queue_sources(
    project_tree: Mapping[str, Any],
) -> tuple[RunnerVNextQueueSource, ...]:
    validated_tree = validate_project_tree(project_tree)
    sources: list[RunnerVNextQueueSource] = []
    for project_id, project in validated_tree["projects"].items():
        execution_modes = project.get("execution_modes") or {}
        if project.get("public") is not True:
            continue
        if project.get("runner_enabled") is not True:
            continue
        if isinstance(execution_modes, Mapping) and execution_modes.get("planning_only") is True:
            continue
        sources.append(
            RunnerVNextQueueSource(
                project_id=str(project_id),
                repository=str(project["repo"]),
            )
        )
    return tuple(sorted(sources, key=lambda source: (source.repository, source.project_id)))


def registered_source_for_repository(
    project_tree: Mapping[str, Any], repository: str
) -> RunnerVNextQueueSource | None:
    try:
        project = get_project_by_repo(project_tree, repository)
    except KeyError:
        return None
    execution_modes = project.get("execution_modes") or {}
    if project.get("public") is not True:
        return None
    if project.get("runner_enabled") is not True:
        return None
    if isinstance(execution_modes, Mapping) and execution_modes.get("planning_only") is True:
        return None
    for project_id, candidate in validate_project_tree(project_tree)["projects"].items():
        if candidate is project or candidate.get("repo") == repository:
            return RunnerVNextQueueSource(str(project_id), repository)
    return None


def get_registered_ready_issue_items(
    project_tree: Mapping[str, Any],
    run_command: RunCommand,
    *,
    ready_label: str,
    json_fields: str = "number,title,body,state,url,closed,labels",
) -> list[RunnerVNextQueueItem]:
    items: list[RunnerVNextQueueItem] = []
    seen: set[tuple[str, int]] = set()
    for source in registered_runner_queue_sources(project_tree):
        code, output = run_command(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                source.repository,
                "--label",
                ready_label,
                "--state",
                "open",
                "--search",
                "is:issue",
                "--json",
                json_fields,
            ]
        )
        if code != 0:
            raise RuntimeError(
                f"gh issue list failed for {source.repository}:\n{output}"
            )
        parsed = json.loads(output or "[]")
        if not isinstance(parsed, list):
            raise RuntimeError(
                f"gh issue list returned non-list JSON for {source.repository}"
            )
        for issue in parsed:
            if not isinstance(issue, dict):
                continue
            number = issue.get("number")
            if not isinstance(number, int) or isinstance(number, bool):
                continue
            identity = (source.repository, number)
            if identity in seen:
                continue
            seen.add(identity)
            items.append(RunnerVNextQueueItem(source=source, issue=dict(issue)))
    return sorted(items, key=runner_vnext_queue_item_priority_key)


def runner_vnext_queue_item_priority_key(
    item: RunnerVNextQueueItem,
) -> tuple[int, str, int]:
    labels = _issue_label_names(item.issue)
    if "queue:RUN_NOW" in labels:
        priority = 0
    elif "runner:priority-1" in labels:
        priority = 1
    else:
        priority = 2
    return priority, item.source_repository, item.issue_number


def _issue_label_names(issue: Mapping[str, Any]) -> frozenset[str]:
    labels = issue.get("labels", [])
    if not isinstance(labels, list):
        return frozenset()
    names: set[str] = set()
    for label in labels:
        if isinstance(label, Mapping) and isinstance(label.get("name"), str):
            names.add(str(label["name"]))
        elif isinstance(label, str):
            names.add(label)
    return frozenset(names)
