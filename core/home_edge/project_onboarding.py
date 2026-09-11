from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping
from urllib.parse import urlparse


SCHEMA_PREFIX = "skeleton.home_edge.project_onboarding.v1"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ProjectOnboardingCode(StrEnum):
    OK = "OK"
    ALREADY_REGISTERED = "ALREADY_REGISTERED"
    MALFORMED_URL = "MALFORMED_URL"
    ACCESS_DENIED = "ACCESS_DENIED"
    CONFLICT = "CONFLICT"
    BOOTSTRAP_REQUIRED = "BOOTSTRAP_REQUIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


class RepositoryKind(StrEnum):
    EMPTY = "EMPTY"
    FORK = "FORK"
    STANDARD = "STANDARD"


class ProjectOnboardingError(ValueError):
    def __init__(self, code: ProjectOnboardingCode, message: str | None = None, *, status: int = 400) -> None:
        super().__init__(message or code.value)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class RepoIdentity:
    host: str
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def canonical_url(self) -> str:
        return f"https://{self.host}/{self.full_name}.git"

    def to_public_mapping(self) -> dict[str, str]:
        return {
            "host": self.host,
            "owner": self.owner,
            "name": self.name,
            "full_name": self.full_name,
            "canonical_url": self.canonical_url,
        }


@dataclass(frozen=True)
class RepositorySnapshot:
    identity: RepoIdentity
    kind: RepositoryKind
    default_branch: str = "main"
    empty: bool = False
    fork: bool = False
    upstream: RepoIdentity | None = None

    def to_public_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "identity": self.identity.to_public_mapping(),
            "kind": self.kind.value,
            "default_branch": self.default_branch,
            "empty": self.empty,
            "fork": self.fork,
        }
        if self.upstream is not None:
            payload["upstream"] = self.upstream.to_public_mapping()
        return payload


@dataclass(frozen=True)
class ProtectedHeadStatus:
    status: str
    pr_number: int
    head_ref: str
    head_sha: str
    base_ref: str = "main"

    def __post_init__(self) -> None:
        if SHA_RE.fullmatch(self.head_sha) is None:
            raise ValueError("head_sha must be a 40 character lowercase hex SHA")

    def to_public_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pr_number": self.pr_number,
            "head_ref": self.head_ref,
            "head_sha": self.head_sha,
            "base_ref": self.base_ref,
        }


@dataclass
class ProjectOnboardingBackend:
    """Canonical server-side policy for Home Edge project onboarding.

    This class deliberately has no GitHub credential inputs. Callers provide
    public repository metadata snapshots; all mutation decisions remain here so
    the mobile adapter cannot bypass bootstrap, fork, or PROJECT_TREE approval
    boundaries.
    """

    repositories: Mapping[str, RepositorySnapshot] = field(default_factory=dict)
    protected_head_status: ProtectedHeadStatus = field(
        default_factory=lambda: ProtectedHeadStatus(
            status="NEEDS_APPROVAL",
            pr_number=4003,
            head_ref="runner/home-edge-project-onboarding",
            head_sha="a" * 40,
        )
    )
    canonical_runtime_ready: bool = False
    registered: set[str] = field(default_factory=set)
    _empty_bootstrapped: set[str] = field(default_factory=set)

    def inspect(self, repo_url: str) -> dict[str, Any]:
        identity = parse_github_repo_url(repo_url)
        snapshot = self._snapshot_for(identity)
        return _public_response(
            action="inspect",
            code=ProjectOnboardingCode.OK,
            repository=snapshot.to_public_mapping(),
            inspection=_inspection_proof(identity),
            mutable=False,
        )

    def bootstrap_empty(
        self,
        *,
        repo_url: str,
        inspected_identity: Mapping[str, Any],
        inspection: Mapping[str, Any],
        action: str,
    ) -> dict[str, Any]:
        if action != "bootstrap_empty_repository":
            raise ProjectOnboardingError(ProjectOnboardingCode.CONFLICT, status=409)
        identity = parse_github_repo_url(repo_url)
        self._require_exact_inspected_identity(identity, inspected_identity, inspection)
        snapshot = self._snapshot_for(identity)
        if snapshot.kind is not RepositoryKind.EMPTY:
            raise ProjectOnboardingError(ProjectOnboardingCode.CONFLICT, status=409)
        self._empty_bootstrapped.add(identity.canonical_url)
        return _public_response(
            action="bootstrap_empty",
            code=ProjectOnboardingCode.OK,
            repository=snapshot.to_public_mapping(),
            bootstrap={"status": "BOOTSTRAPPED"},
            mutable=True,
        )

    def prepare_register(
        self,
        *,
        repo_url: str,
        mutate_upstream: bool = False,
        project_tree_approved: bool = False,
    ) -> dict[str, Any]:
        identity = parse_github_repo_url(repo_url)
        snapshot = self._snapshot_for(identity)
        self._ensure_registerable(snapshot, mutate_upstream=mutate_upstream)
        if not project_tree_approved:
            return _public_response(
                action="prepare_register",
                code=ProjectOnboardingCode.APPROVAL_REQUIRED,
                repository=snapshot.to_public_mapping(),
                protected=self.protected_head_status.to_public_mapping(),
                mutable=False,
            )
        return _public_response(
            action="prepare_register",
            code=ProjectOnboardingCode.OK,
            repository=snapshot.to_public_mapping(),
            protected=self.protected_head_status.to_public_mapping(),
            mutable=False,
        )

    def register(
        self,
        *,
        repo_url: str,
        mutate_upstream: bool = False,
        project_tree_approved: bool = False,
    ) -> dict[str, Any]:
        identity = parse_github_repo_url(repo_url)
        snapshot = self._snapshot_for(identity)
        self._ensure_registerable(snapshot, mutate_upstream=mutate_upstream)
        if identity.canonical_url in self.registered:
            return _public_response(
                action="register",
                code=ProjectOnboardingCode.ALREADY_REGISTERED,
                repository=snapshot.to_public_mapping(),
                registration={"status": "ALREADY_REGISTERED"},
                mutable=False,
            )
        if not project_tree_approved or not self.canonical_runtime_ready:
            return _public_response(
                action="register",
                code=ProjectOnboardingCode.APPROVAL_REQUIRED,
                repository=snapshot.to_public_mapping(),
                protected=self.protected_head_status.to_public_mapping(),
                registration={"status": "NEEDS_APPROVAL"},
                mutable=False,
            )
        self.registered.add(identity.canonical_url)
        return _public_response(
            action="register",
            code=ProjectOnboardingCode.OK,
            repository=snapshot.to_public_mapping(),
            registration={"status": "ACTIVE"},
            mutable=True,
        )

    def readiness(self) -> dict[str, Any]:
        return _public_response(
            action="readiness",
            code=ProjectOnboardingCode.OK,
            readiness={
                "status": "READY" if self.canonical_runtime_ready else "PENDING_CANONICAL_RUNTIME",
                "canonical_runtime_ready": self.canonical_runtime_ready,
            },
            mutable=False,
        )

    def status(self, repo_url: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "registered_count": len(self.registered),
            "read_only": True,
            "canonical_runtime_ready": self.canonical_runtime_ready,
        }
        if repo_url:
            identity = parse_github_repo_url(repo_url)
            payload["repository"] = identity.to_public_mapping()
            payload["registered"] = identity.canonical_url in self.registered
        return _public_response(action="status", code=ProjectOnboardingCode.OK, status=payload, mutable=False)

    def _snapshot_for(self, identity: RepoIdentity) -> RepositorySnapshot:
        return self.repositories.get(
            identity.canonical_url,
            RepositorySnapshot(identity=identity, kind=RepositoryKind.STANDARD),
        )

    def _require_exact_inspected_identity(
        self,
        identity: RepoIdentity,
        inspected_identity: Mapping[str, Any],
        inspection: Mapping[str, Any],
    ) -> None:
        expected = identity.to_public_mapping()
        supplied = {key: str(inspected_identity.get(key, "")) for key in expected}
        if supplied != expected:
            raise ProjectOnboardingError(ProjectOnboardingCode.CONFLICT, status=409)
        if dict(inspection) != _inspection_proof(identity):
            raise ProjectOnboardingError(ProjectOnboardingCode.CONFLICT, status=409)

    def _ensure_registerable(self, snapshot: RepositorySnapshot, *, mutate_upstream: bool) -> None:
        if snapshot.kind is RepositoryKind.EMPTY and snapshot.identity.canonical_url not in self._empty_bootstrapped:
            raise ProjectOnboardingError(ProjectOnboardingCode.BOOTSTRAP_REQUIRED, status=409)
        if snapshot.kind is RepositoryKind.FORK and mutate_upstream:
            raise ProjectOnboardingError(ProjectOnboardingCode.ACCESS_DENIED, status=403)


def parse_github_repo_url(repo_url: str) -> RepoIdentity:
    raw = str(repo_url or "").strip()
    if not raw:
        raise ProjectOnboardingError(ProjectOnboardingCode.MALFORMED_URL)
    if raw.startswith("git@github.com:"):
        path = raw.removeprefix("git@github.com:")
        host = "github.com"
    else:
        parsed = urlparse(raw)
        if parsed.scheme not in {"https", "http"} or parsed.netloc.lower() != "github.com":
            raise ProjectOnboardingError(ProjectOnboardingCode.MALFORMED_URL)
        host = parsed.netloc.lower()
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2 or not all(_valid_github_part(part) for part in parts):
        raise ProjectOnboardingError(ProjectOnboardingCode.MALFORMED_URL)
    return RepoIdentity(host=host, owner=parts[0], name=parts[1])


def _valid_github_part(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value)) and not value.startswith(".")


def _inspection_proof(identity: RepoIdentity) -> dict[str, str]:
    digest = hashlib.sha256(f"home-edge-project-inspect-v1:{identity.canonical_url}".encode("utf-8")).hexdigest()
    return {"source": "inspect", "identity_sha256": digest}


def _public_response(action: str, code: ProjectOnboardingCode, **payload: Any) -> dict[str, Any]:
    response = {
        "schema": f"{SCHEMA_PREFIX}.response",
        "action": action,
        "code": code.value,
    }
    response.update(payload)
    return redact_public_metadata(response)


def error_response(error: ProjectOnboardingError) -> dict[str, Any]:
    return _public_response("error", error.code, error={"code": error.code.value})


def redact_public_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if any(token in lower for token in ("token", "secret", "credential", "stderr", "local_path", "path")):
                continue
            redacted[str(key)] = redact_public_metadata(item)
        return redacted
    if isinstance(value, list):
        return [redact_public_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [redact_public_metadata(item) for item in value]
    return value
