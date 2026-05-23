from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Union

import yaml


REGISTRY_SCHEMA = "skeleton.project_registry.v1"
PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REQUIRED_ENTRY_FIELDS = (
    "project_id",
    "repository",
    "checkout_path",
    "worktree_root",
    "base_branch",
    "runner_modes",
    "enabled",
)


@dataclass(frozen=True)
class ProjectRegistryEntry:
    project_id: str
    repository: str
    checkout_path: Path
    worktree_root: Path
    base_branch: str
    runner_modes: tuple[str, ...]
    enabled: bool


def load_project_registry(path: Union[str, Path]) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return validate_project_registry(data)


def validate_project_registry(registry: object) -> dict[str, Any]:
    if not isinstance(registry, dict):
        raise ValueError("project registry must be a mapping.")
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise ValueError(f"project registry schema must be {REGISTRY_SCHEMA!r}.")

    projects = registry.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise ValueError("project registry projects must be a non-empty mapping.")

    default_project = registry.get("default_project")
    _validate_project_id(default_project)
    if default_project not in projects:
        raise ValueError("default_project must reference a registry entry.")

    repositories: set[str] = set()
    for key, raw_entry in projects.items():
        _validate_project_id(key)
        entry = _validate_entry(key, raw_entry)
        if entry.repository in repositories:
            raise ValueError(f"repository {entry.repository!r} is duplicated.")
        repositories.add(entry.repository)

    return registry


def registry_entry(
    registry: Mapping[str, Any], project_id: str
) -> ProjectRegistryEntry:
    validated = validate_project_registry(dict(registry))
    _validate_project_id(project_id)
    raw_entry = validated["projects"].get(project_id)
    if raw_entry is None:
        raise KeyError(f"unknown project_id {project_id!r}.")
    return _entry_from_mapping(raw_entry)


def registry_entry_for_repository(
    registry: Mapping[str, Any], repository: str
) -> ProjectRegistryEntry:
    validated = validate_project_registry(dict(registry))
    _validate_repository(repository)
    for raw_entry in validated["projects"].values():
        entry = _entry_from_mapping(raw_entry)
        if entry.repository == repository:
            return entry
    raise KeyError(f"unknown repository {repository!r}.")


def resolve_registry_target(
    registry: Mapping[str, Any],
    *,
    target_project: str | None = None,
    target_repository: str | None = None,
) -> ProjectRegistryEntry:
    validated = validate_project_registry(dict(registry))
    if target_project is None and target_repository is None:
        target_project = str(validated["default_project"])

    project_entry = (
        registry_entry(validated, target_project) if target_project is not None else None
    )
    repository_entry = (
        registry_entry_for_repository(validated, target_repository)
        if target_repository is not None
        else None
    )
    if project_entry is not None and repository_entry is not None:
        if project_entry.project_id != repository_entry.project_id:
            raise ValueError("Target Project and Target Repository reference different registry entries.")
        return project_entry
    entry = project_entry or repository_entry
    if entry is None:
        return registry_entry(validated, str(validated["default_project"]))
    return entry


def validate_registry_entry_ready(
    entry: ProjectRegistryEntry,
    *,
    remote_reader: Callable[[Path], str],
) -> None:
    if not entry.enabled:
        raise ValueError(f"project {entry.project_id!r} is disabled.")
    if not entry.checkout_path.is_dir():
        raise ValueError(f"checkout_path does not exist for project {entry.project_id!r}.")
    remote = remote_reader(entry.checkout_path).strip()
    if not remote_matches_repository(remote, entry.repository):
        raise ValueError(f"checkout remote does not match registry repository for project {entry.project_id!r}.")


def remote_matches_repository(remote: str, repository: str) -> bool:
    candidates = {
        repository,
        f"https://github.com/{repository}",
        f"https://github.com/{repository}.git",
        f"git@github.com:{repository}.git",
        f"ssh://git@github.com/{repository}.git",
    }
    return remote.rstrip("/") in candidates


def ensure_issue_worktree_path(entry: ProjectRegistryEntry, issue_number: int) -> Path:
    if issue_number < 1:
        raise ValueError("issue_number must be positive.")
    return ensure_safe_issue_worktree_path(entry, entry.worktree_root / f"issue-{issue_number}")


def ensure_safe_issue_worktree_path(
    entry: ProjectRegistryEntry, path: str | Path
) -> Path:
    root = _safe_absolute_path(entry.worktree_root, "worktree_root")
    candidate = Path(path).expanduser().resolve()
    if candidate == root:
        raise ValueError(f"Refusing to use worktree root as issue worktree: {candidate}")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Refusing worktree path outside configured root {root}: {candidate}"
        ) from exc
    return candidate


def _validate_entry(project_id: str, entry: object) -> ProjectRegistryEntry:
    if not isinstance(entry, dict):
        raise ValueError(f"project {project_id!r} registry entry must be a mapping.")
    missing = [field for field in REQUIRED_ENTRY_FIELDS if field not in entry]
    if missing:
        raise ValueError(
            f"project {project_id!r} registry entry is missing fields: {', '.join(missing)}."
        )
    parsed = _entry_from_mapping(entry)
    if parsed.project_id != project_id:
        raise ValueError(f"project {project_id!r} registry key must match project_id.")
    return parsed


def _entry_from_mapping(entry: Mapping[str, Any]) -> ProjectRegistryEntry:
    project_id = entry.get("project_id")
    _validate_project_id(project_id)
    repository = entry.get("repository")
    _validate_repository(repository)

    checkout_path = _safe_absolute_path(entry.get("checkout_path"), "checkout_path")
    worktree_root = _safe_absolute_path(entry.get("worktree_root"), "worktree_root")
    if checkout_path == worktree_root:
        raise ValueError(f"project {project_id!r} checkout_path and worktree_root must differ.")

    base_branch = entry.get("base_branch")
    if not isinstance(base_branch, str) or base_branch.strip() == "":
        raise ValueError(f"project {project_id!r} base_branch must be a non-empty string.")

    runner_modes = entry.get("runner_modes")
    if (
        not isinstance(runner_modes, list)
        or not runner_modes
        or not all(isinstance(mode, str) and mode.strip() for mode in runner_modes)
    ):
        raise ValueError(f"project {project_id!r} runner_modes must be a non-empty string list.")

    enabled = entry.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError(f"project {project_id!r} enabled must be a boolean.")

    return ProjectRegistryEntry(
        project_id=str(project_id),
        repository=str(repository),
        checkout_path=checkout_path,
        worktree_root=worktree_root,
        base_branch=base_branch,
        runner_modes=tuple(runner_modes),
        enabled=enabled,
    )


def _safe_absolute_path(value: object, field: str) -> Path:
    if isinstance(value, Path):
        path = value.expanduser()
    elif isinstance(value, str) and value.strip():
        path = Path(value).expanduser()
    else:
        raise ValueError(f"{field} must be a non-empty path.")
    if not path.is_absolute():
        raise ValueError(f"{field} must be absolute.")
    if ".." in path.parts:
        raise ValueError(f"{field} must not contain parent traversal.")
    return path.resolve()


def _validate_project_id(project_id: object) -> None:
    if not isinstance(project_id, str) or PROJECT_ID_PATTERN.fullmatch(project_id) is None:
        raise ValueError(f"invalid project_id {project_id!r}.")


def _validate_repository(repository: object) -> None:
    if not isinstance(repository, str) or REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise ValueError(f"invalid repository {repository!r}.")
