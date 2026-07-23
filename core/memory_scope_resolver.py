from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Mapping


SAFE_SCOPE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class MemoryScopeResolutionError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ExactMemoryScope:
    project_id: str
    dataset_id: str
    repository: str
    branch: str
    task_transition_hash: str

    @property
    def cache_key(self) -> str:
        material = "\n".join(
            (
                self.project_id,
                self.dataset_id,
                self.repository,
                self.branch,
                self.task_transition_hash,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def resolve_exact_memory_scope(value: Mapping[str, object]) -> ExactMemoryScope:
    if not isinstance(value, Mapping):
        raise MemoryScopeResolutionError("EXACT_SCOPE_REQUIRED", "exact memory scope is required")
    return ExactMemoryScope(
        project_id=_scope_id(value.get("project_id"), "project_id"),
        dataset_id=_scope_id(value.get("dataset_id"), "dataset_id"),
        repository=_repository(value.get("repository")),
        branch=_branch(value.get("branch")),
        task_transition_hash=_sha256(value.get("task_transition_hash"), "task_transition_hash"),
    )


def task_transition_hash(task_body: str) -> str:
    return hashlib.sha256(task_body.encode("utf-8")).hexdigest()


def _scope_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not SAFE_SCOPE_ID_RE.fullmatch(value):
        raise MemoryScopeResolutionError("EXACT_SCOPE_INVALID", f"{field} is malformed")
    if value in {"*", "all", "any", ".", ".."} or "*" in value or "/" in value or "\\" in value or ".." in value:
        raise MemoryScopeResolutionError("EXACT_SCOPE_INVALID", f"{field} must be exact")
    return value


def _repository(value: object) -> str:
    if not isinstance(value, str) or len(value) > 160 or value.count("/") != 1:
        raise MemoryScopeResolutionError("EXACT_SCOPE_INVALID", "repository is malformed")
    owner, repo = value.split("/", 1)
    _scope_id(owner, "repository_owner")
    _scope_id(repo, "repository_name")
    return value


def _branch(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise MemoryScopeResolutionError("EXACT_SCOPE_INVALID", "branch is malformed")
    if value.startswith(("/", "~")) or "\\" in value or ".." in value or "*" in value:
        raise MemoryScopeResolutionError("EXACT_SCOPE_INVALID", "branch must be exact")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,159}", value):
        raise MemoryScopeResolutionError("EXACT_SCOPE_INVALID", "branch contains unsupported characters")
    return value


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Fa-f0-9]{64}", value):
        raise MemoryScopeResolutionError("EXACT_SCOPE_INVALID", f"{field} must be sha256")
    return value.lower()
