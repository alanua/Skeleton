from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping


class MemoryScopeError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class MemoryTransitionScope:
    project_id: str
    dataset_id: str
    repository: str
    branch: str
    task_transition_hash: str

    @property
    def cache_key(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "project_id": self.project_id,
                    "dataset_id": self.dataset_id,
                    "repository": self.repository,
                    "branch": self.branch,
                    "task_transition_hash": self.task_transition_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


_PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_.-]{1,120}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,180}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


def resolve_memory_transition_scope(request: Mapping[str, object]) -> MemoryTransitionScope:
    project = _token(request.get("project_id"), _PROJECT_RE, "INVALID_PROJECT")
    dataset = _token(request.get("dataset_id"), _DATASET_RE, "INVALID_DATASET")
    repository = _token(request.get("repository"), _REPO_RE, "INVALID_REPOSITORY")
    branch = _token(request.get("branch"), _BRANCH_RE, "INVALID_REF")
    task_hash = request.get("task_transition_hash")
    if not isinstance(task_hash, str) or _HASH_RE.fullmatch(task_hash) is None:
        raise MemoryScopeError("INVALID_TASK_TRANSITION_HASH", "task transition hash is malformed")
    for label, value in {
        "project": project,
        "dataset": dataset,
        "repository": repository,
        "branch": branch,
    }.items():
        if "*" in value:
            raise MemoryScopeError("WILDCARD_SCOPE_FORBIDDEN", f"{label} wildcard is forbidden")
        if ".." in value or value.startswith("/") or "\\" in value:
            raise MemoryScopeError("TRAVERSAL_SCOPE_FORBIDDEN", f"{label} traversal is forbidden")
    if repository != "alanua/Skeleton":
        raise MemoryScopeError("FOREIGN_REPOSITORY_FORBIDDEN", "repository is not authorized for this bootstrap")
    if project != "skeleton" or dataset != "skeleton":
        raise MemoryScopeError("FOREIGN_SCOPE_FORBIDDEN", "project/dataset scope is not authorized")
    return MemoryTransitionScope(
        project_id=project,
        dataset_id=dataset,
        repository=repository,
        branch=branch,
        task_transition_hash=task_hash,
    )


def task_transition_hash(task_content: str) -> str:
    if not isinstance(task_content, str) or not task_content.strip():
        raise MemoryScopeError("TASK_CONTENT_REQUIRED", "task content is mandatory")
    return hashlib.sha256(task_content.encode("utf-8")).hexdigest()


def _token(value: object, pattern: re.Pattern[str], reason_code: str) -> str:
    if not isinstance(value, str) or not value or pattern.fullmatch(value) is None:
        raise MemoryScopeError(reason_code, "scope token is malformed")
    return value
