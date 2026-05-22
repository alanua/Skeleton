from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping, Union

import yaml


PROJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
WORKTREE_PREFIX_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WORKTREE_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
REQUIRED_PROJECT_FIELDS = (
    "repo",
    "public",
    "future_parallel_worktrees",
    "runtime_approval_required",
    "worktree_name_prefix",
)


def load_project_tree(path: Union[str, Path]) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return validate_project_tree(data)


def validate_project_tree(project_tree: object) -> dict[str, Any]:
    if not isinstance(project_tree, dict):
        raise ValueError("project tree must be a mapping.")

    version = project_tree.get("version")
    if not isinstance(version, str) or version.strip() == "":
        raise ValueError("project tree version must be a non-empty string.")

    projects = project_tree.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise ValueError("project tree projects must be a non-empty mapping.")

    default_project = project_tree.get("default_project")
    _validate_project_id(default_project)
    if default_project not in projects:
        raise ValueError("default_project must reference a declared project.")

    for project_id, project in projects.items():
        _validate_project_id(project_id)
        _validate_project(project_id, project)

    return project_tree


def get_project(project_tree: Mapping[str, Any], project_id: str) -> dict[str, Any]:
    validated_tree = validate_project_tree(project_tree)
    _validate_project_id(project_id)

    project = validated_tree["projects"].get(project_id)
    if project is None:
        raise KeyError(f"unknown project_id {project_id!r}.")
    return project


def plan_worktree_name(project_id: str, task_ref: str) -> str:
    _validate_project_id(project_id)
    if not isinstance(task_ref, str) or task_ref.strip() == "":
        raise ValueError("task_ref must be a non-empty string.")

    task_slug = WORKTREE_SLUG_PATTERN.sub("-", task_ref.strip().lower()).strip("-")
    if task_slug == "":
        raise ValueError("task_ref must contain at least one letter or digit.")

    digest = hashlib.sha256(task_ref.encode("utf-8")).hexdigest()[:8]
    return f"{project_id.replace('_', '-')}-{task_slug[:48]}-{digest}"


def _validate_project_id(project_id: object) -> None:
    if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError(f"invalid project_id {project_id!r}.")


def _validate_project(project_id: str, project: object) -> None:
    if not isinstance(project, dict):
        raise ValueError(f"project {project_id!r} must be a mapping.")

    missing = [field for field in REQUIRED_PROJECT_FIELDS if field not in project]
    if missing:
        raise ValueError(f"project {project_id!r} is missing fields: {', '.join(missing)}.")

    if not isinstance(project["repo"], str) or project["repo"].strip() == "":
        raise ValueError(f"project {project_id!r} repo must be a non-empty string.")

    for field in ("public", "future_parallel_worktrees", "runtime_approval_required"):
        if not isinstance(project[field], bool):
            raise ValueError(f"project {project_id!r} {field} must be a boolean.")

    prefix = project["worktree_name_prefix"]
    if not isinstance(prefix, str) or not WORKTREE_PREFIX_PATTERN.fullmatch(prefix):
        raise ValueError(f"project {project_id!r} worktree_name_prefix is invalid.")
