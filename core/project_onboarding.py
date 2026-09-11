from __future__ import annotations

import copy
import hashlib
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.project_tree import (
    DEFAULT_APPROVED_WORKSPACE_ROOT,
    propose_project_registration,
    validate_project_tree,
)


GITHUB_HOST = "github.com"
DEFAULT_EMPTY_BOOTSTRAP_BRANCH = "main"
PUBLIC_SAFE_SCHEMA_VERSION = "project_onboarding.public_result.v1"
READINESS_CANARY_CONTRACT_VERSION = "project_onboarding.readiness_canary.v1"
BOOTSTRAP_CONTRACT_VERSION = "project_onboarding.empty_bootstrap.v1"
PROTECTED_REGISTRATION_MODE = "protected_draft_pr"
BOOTSTRAP_ACTION = "BOOTSTRAP_EMPTY_REPOSITORY"
BOOTSTRAP_ALLOWED_FILES = frozenset(("README.md", ".gitignore"))
BOOTSTRAP_REQUIRED_FILES = frozenset(("README.md",))
OWNER_RE = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
REPO_RE = r"[A-Za-z0-9._-]+"
GITHUB_SLUG_RE = re.compile(rf"^(?P<owner>{OWNER_RE})/(?P<repo>{REPO_RE})$")
SAFE_TOKEN_RE = re.compile(r"[^a-z0-9]+")
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PRIVATE_PATH_REDACTION = "<redacted-private-runner-path>"


class ProjectOnboardingError(ValueError):
    """Fail-closed onboarding validation error."""


@dataclass(frozen=True)
class CanonicalRepository:
    slug: str
    owner: str
    name: str
    private: bool


def inspect_project_onboarding(
    repository_metadata: Mapping[str, Any],
    project_tree: Mapping[str, Any],
) -> dict[str, Any]:
    """Inspect public-safe GitHub metadata and return a deterministic decision.

    This function is deliberately side-effect-free. It never checks out a repo,
    synchronizes branches, writes PROJECT_TREE, or mutates fork upstreams.
    """

    try:
        repository = _canonical_repository_from_metadata(repository_metadata)
        classification = classify_repository(repository_metadata)
        upstream = _fork_upstream(repository_metadata) if classification == "FORK" else None
        existing = _existing_project_for_repo(project_tree, repository.slug)
        if existing is not None:
            project_id, project = existing
            return _public_result(
                status="ALREADY_REGISTERED",
                repository=repository,
                classification=classification,
                private=repository.private,
                project_id=project_id,
                registration_proposal=None,
                upstream=upstream,
                reason="exact_repository_already_registered",
                registered_project=_public_project(project, repository.private),
            )

        if classification == "EMPTY":
            return _public_result(
                status="NEEDS_BOOTSTRAP",
                repository=repository,
                classification=classification,
                private=repository.private,
                project_id=derive_project_id(repository.slug),
                registration_proposal=None,
                upstream=None,
                reason="empty_repository_requires_explicit_bootstrap_before_first_commit",
                bootstrap_contract=empty_repository_bootstrap_contract(repository.slug),
            )

        proposal = propose_registration(repository_metadata, project_tree)
        return _public_result(
            status="PROPOSE_REGISTRATION",
            repository=repository,
            classification=classification,
            private=repository.private,
            project_id=proposal["project_id"],
            registration_proposal=proposal,
            upstream=upstream,
            reason="protected_project_tree_registration_required",
        )
    except ProjectOnboardingError as exc:
        return _blocked_result(str(exc))
    except ValueError as exc:
        return _blocked_result(str(exc))


def classify_repository(repository_metadata: Mapping[str, Any]) -> str:
    _canonical_repository_from_metadata(repository_metadata)
    if _bool_field(repository_metadata, "fork") or _bool_field(repository_metadata, "is_fork"):
        _fork_upstream(repository_metadata)
        return "FORK"
    if _repository_is_empty(repository_metadata):
        return "EMPTY"
    return "STANDARD"


def propose_registration(
    repository_metadata: Mapping[str, Any],
    project_tree: Mapping[str, Any],
) -> dict[str, Any]:
    repository = _canonical_repository_from_metadata(repository_metadata)
    classification = classify_repository(repository_metadata)
    if classification == "EMPTY":
        raise ProjectOnboardingError("empty_repository_requires_explicit_bootstrap")

    default_branch = _string_field(repository_metadata, "default_branch")
    head_sha = _head_sha(repository_metadata)
    if default_branch is None:
        raise ProjectOnboardingError("default_branch_required")
    if head_sha is None:
        raise ProjectOnboardingError("default_branch_head_sha_required")

    upstream = _fork_upstream(repository_metadata) if classification == "FORK" else None
    project_id = derive_project_id(repository.slug)
    project_entry = build_project_tree_entry(repository)
    proposed_tree = propose_project_registration(project_tree, project_id, project_entry)
    proposed_project = proposed_tree["projects"][project_id]

    return {
        "action": "PROPOSE_PROJECT_TREE_REGISTRATION",
        "mode": PROTECTED_REGISTRATION_MODE,
        "protected_pr_required": True,
        "direct_main_mutation": False,
        "runtime_sync": False,
        "repository": repository.slug,
        "project_id": project_id,
        "classification": classification,
        "default_branch": default_branch,
        "head_sha": head_sha,
        "writable_repository": repository.slug,
        "mutation_target_repository": repository.slug,
        "upstream": upstream,
        "upstream_read_only": upstream is not None,
        "project_tree_entry": _public_project(proposed_project, repository.private),
        "project_tree_entry_sha256": _stable_hash(proposed_project),
        "proposed_project_tree_sha256": _stable_hash(proposed_tree),
        "readiness_canary": readiness_canary_contract(repository.slug, default_branch, head_sha),
    }


def validate_empty_bootstrap_packet(
    packet: Mapping[str, Any],
    repository_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    repository = _canonical_repository_from_metadata(repository_metadata)
    classification = classify_repository(repository_metadata)
    if classification != "EMPTY":
        return _bootstrap_blocked("repository_not_empty", repository.slug)

    if packet.get("action") != BOOTSTRAP_ACTION:
        return _bootstrap_blocked("bootstrap_action_required", repository.slug)
    if _canonical_github_slug(packet.get("repository")) != repository.slug:
        return _bootstrap_blocked("repository_mismatch", repository.slug)
    if packet.get("target_repository") not in (None, repository.slug):
        return _bootstrap_blocked("target_repository_mismatch", repository.slug)
    if packet.get("branch") != DEFAULT_EMPTY_BOOTSTRAP_BRANCH:
        return _bootstrap_blocked("bootstrap_branch_must_be_main", repository.slug)
    if packet.get("initial_commit") is not True:
        return _bootstrap_blocked("initial_commit_required", repository.slug)

    files = packet.get("files")
    if not isinstance(files, Sequence) or isinstance(files, (str, bytes)):
        return _bootstrap_blocked("bootstrap_files_required", repository.slug)
    file_names: list[str] = []
    for item in files:
        if isinstance(item, str):
            name = item
        elif isinstance(item, Mapping) and isinstance(item.get("path"), str):
            name = str(item["path"])
        else:
            return _bootstrap_blocked("bootstrap_file_path_invalid", repository.slug)
        if name.startswith("/") or ".." in Path(name).parts:
            return _bootstrap_blocked("bootstrap_file_path_invalid", repository.slug)
        file_names.append(name)

    file_set = frozenset(file_names)
    if not BOOTSTRAP_REQUIRED_FILES <= file_set:
        return _bootstrap_blocked("bootstrap_readme_required", repository.slug)
    if not file_set <= BOOTSTRAP_ALLOWED_FILES:
        return _bootstrap_blocked("bootstrap_files_not_minimal", repository.slug)

    return {
        "schema": BOOTSTRAP_CONTRACT_VERSION,
        "status": "BOOTSTRAP_ALLOWED",
        "repository": repository.slug,
        "branch": DEFAULT_EMPTY_BOOTSTRAP_BRANCH,
        "allowed_files": sorted(file_set),
        "commit_shape": "initial_main_readme_optional_gitignore",
        "writable_repository": repository.slug,
        "upstream_mutation_allowed": False,
        "direct_project_tree_mutation": False,
    }


def validate_mutation_target(
    repository_metadata: Mapping[str, Any],
    target_repository: str,
) -> dict[str, Any]:
    repository = _canonical_repository_from_metadata(repository_metadata)
    target = _canonical_github_slug(target_repository)
    upstream = _fork_upstream(repository_metadata) if classify_repository(repository_metadata) == "FORK" else None
    if target != repository.slug:
        reason = "upstream_mutation_blocked" if upstream and target == upstream["repository"] else "target_repository_mismatch"
        return {
            "status": "BLOCKED",
            "reason": reason,
            "writable_repository": repository.slug,
            "target_repository": target,
            "upstream_read_only": upstream is not None,
        }
    return {
        "status": "ALLOWED",
        "writable_repository": repository.slug,
        "target_repository": target,
        "upstream_read_only": upstream is not None,
    }


def build_project_tree_entry(repository: CanonicalRepository) -> dict[str, Any]:
    slug = derive_path_slug(repository.slug)
    root = DEFAULT_APPROVED_WORKSPACE_ROOT / "worktrees" / slug
    public = not repository.private
    return {
        "repo": repository.slug,
        "checkout_path": str(root / "main"),
        "worktree_root": str(root),
        "public": public,
        "runner_enabled": public,
        "execution_modes": {
            "planning_only": not public,
            "codex_issue_worktree": public,
            "live_cross_repo": False,
        },
        "requires_explicit_approval_for_mode_change": True,
        "future_parallel_worktrees": public,
        "runtime_approval_required": True,
        "worktree_name_prefix": slug,
        "description": (
            f"{'Private' if repository.private else 'Public'} GitHub project "
            "proposed by fail-closed Runner onboarding."
        ),
    }


def empty_repository_bootstrap_contract(repository: str) -> dict[str, Any]:
    return {
        "schema": BOOTSTRAP_CONTRACT_VERSION,
        "repository": _canonical_github_slug(repository),
        "action": BOOTSTRAP_ACTION,
        "branch": DEFAULT_EMPTY_BOOTSTRAP_BRANCH,
        "initial_commit_required": True,
        "allowed_files": sorted(BOOTSTRAP_ALLOWED_FILES),
        "required_files": sorted(BOOTSTRAP_REQUIRED_FILES),
        "registration_after_bootstrap": PROTECTED_REGISTRATION_MODE,
    }


def readiness_canary_contract(repository: str, default_branch: str, head_sha: str) -> dict[str, Any]:
    return {
        "schema": READINESS_CANARY_CONTRACT_VERSION,
        "repository": _canonical_github_slug(repository),
        "default_branch": default_branch,
        "head_sha": _validate_head_sha(head_sha),
        "mode": "read_only",
        "checks": (
            "registered_project_tree_entry_present",
            "github_default_branch_head_matches",
            "runner_queue_read_access_only",
        ),
        "mutation_allowed": False,
    }


def derive_project_id(repository: str) -> str:
    slug = derive_path_slug(repository).replace("-", "_")
    if not slug or not re.fullmatch(r"[a-z][a-z0-9_]*", slug):
        digest = hashlib.sha256(repository.encode("utf-8")).hexdigest()[:8]
        slug = f"project_{digest}"
    return slug


def derive_path_slug(repository: str) -> str:
    canonical = _canonical_github_slug(repository)
    owner, name = canonical.split("/", 1)
    slug = SAFE_TOKEN_RE.sub("-", f"{owner}-{name}".lower()).strip("-")
    if not slug:
        raise ProjectOnboardingError("repository_slug_empty")
    return slug


def _canonical_repository_from_metadata(metadata: Mapping[str, Any]) -> CanonicalRepository:
    _validate_repository_identity_fields(metadata)
    repo_value = (
        metadata.get("full_name")
        or metadata.get("repository")
        or metadata.get("nameWithOwner")
        or metadata.get("html_url")
        or metadata.get("clone_url")
        or metadata.get("ssh_url")
    )
    slug = _canonical_github_slug(repo_value)
    owner, name = slug.split("/", 1)
    private = _bool_field(metadata, "private") or _bool_field(metadata, "is_private")
    return CanonicalRepository(slug=slug, owner=owner, name=name, private=private)


def _validate_repository_identity_fields(metadata: Mapping[str, Any]) -> None:
    slugs: set[str] = set()
    for field in ("full_name", "repository", "nameWithOwner", "html_url", "clone_url", "ssh_url"):
        value = metadata.get(field)
        if value is None:
            continue
        slugs.add(_canonical_github_slug(value))
    if len(slugs) > 1:
        raise ProjectOnboardingError("repository_identity_fields_conflict")


def _canonical_github_slug(value: object) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ProjectOnboardingError("canonical_github_repository_required")
    raw = value.strip()
    if any(marker in raw.lower() for marker in ("token=", "access_token", "password=", "@github.com/")) and raw.startswith("http"):
        raise ProjectOnboardingError("repository_identity_contains_credentials")
    if raw.startswith("git@github.com:"):
        path = raw.removeprefix("git@github.com:")
        return _validate_slug(path.removesuffix(".git"))
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme:
        if parsed.scheme not in ("https", "http") or parsed.netloc.lower() != GITHUB_HOST:
            raise ProjectOnboardingError("canonical_github_repository_required")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProjectOnboardingError("repository_identity_must_not_include_credentials_or_query")
        path_parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(path_parts) != 2:
            raise ProjectOnboardingError("canonical_github_repository_required")
        return _validate_slug("/".join(path_parts).removesuffix(".git"))
    return _validate_slug(raw.removesuffix(".git"))


def _validate_slug(slug: str) -> str:
    match = GITHUB_SLUG_RE.fullmatch(slug)
    if match is None:
        raise ProjectOnboardingError("canonical_github_repository_required")
    return f"{match.group('owner')}/{match.group('repo')}"


def _repository_is_empty(metadata: Mapping[str, Any]) -> bool:
    if _bool_field(metadata, "empty") or _bool_field(metadata, "is_empty"):
        return True
    default_branch = _string_field(metadata, "default_branch")
    head_sha = _head_sha(metadata)
    size = metadata.get("size")
    if default_branch is None and head_sha is None:
        return True
    if isinstance(size, int) and not isinstance(size, bool) and size == 0 and head_sha is None:
        return True
    return False


def _fork_upstream(metadata: Mapping[str, Any]) -> dict[str, Any]:
    upstream_raw = metadata.get("parent") or metadata.get("source") or metadata.get("upstream")
    if not isinstance(upstream_raw, Mapping):
        raise ProjectOnboardingError("fork_upstream_required")
    upstream_repo = _canonical_repository_from_metadata(upstream_raw)
    if upstream_repo.slug == _canonical_repository_from_metadata(metadata).slug:
        raise ProjectOnboardingError("fork_upstream_must_differ")
    return {
        "repository": upstream_repo.slug,
        "read_only": True,
        "mutation_allowed": False,
    }


def _head_sha(metadata: Mapping[str, Any]) -> str | None:
    for field in (
        "head_sha",
        "default_branch_head_sha",
        "defaultBranchHeadSha",
        "pushed_head_sha",
    ):
        value = metadata.get(field)
        if value is None:
            continue
        return _validate_head_sha(value)
    return None


def _validate_head_sha(value: object) -> str:
    if not isinstance(value, str) or HEX_SHA_RE.fullmatch(value) is None:
        raise ProjectOnboardingError("default_branch_head_sha_invalid")
    return value


def _string_field(mapping: Mapping[str, Any], field: str) -> str | None:
    value = mapping.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _bool_field(mapping: Mapping[str, Any], field: str) -> bool:
    value = mapping.get(field)
    return isinstance(value, bool) and value


def _existing_project_for_repo(
    project_tree: Mapping[str, Any], repository: str
) -> tuple[str, Mapping[str, Any]] | None:
    validated = validate_project_tree(copy.deepcopy(project_tree))
    for project_id, project in validated["projects"].items():
        if project.get("repo") == repository:
            return str(project_id), project
    return None


def _public_result(
    *,
    status: str,
    repository: CanonicalRepository,
    classification: str,
    private: bool,
    project_id: str | None,
    registration_proposal: Mapping[str, Any] | None,
    upstream: Mapping[str, Any] | None,
    reason: str,
    bootstrap_contract: Mapping[str, Any] | None = None,
    registered_project: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": PUBLIC_SAFE_SCHEMA_VERSION,
        "status": status,
        "reason": reason,
        "repository": repository.slug,
        "classification": classification,
        "project_id": project_id,
        "private": private,
        "public_safe": True,
        "credentials_exposed": False,
        "local_paths_exposed": not private,
        "cloud_codegen_enabled": False if private else True,
        "runtime_sync": False,
        "direct_main_mutation": False,
        "protected_pr_required": status == "PROPOSE_REGISTRATION",
        "fork_upstream": upstream,
        "writable_repository": repository.slug,
        "upstream_mutation_allowed": False,
    }
    if registration_proposal is not None:
        result["registration_proposal"] = _redact_private_proposal(registration_proposal, private)
    if bootstrap_contract is not None:
        result["bootstrap_contract"] = dict(bootstrap_contract)
    if registered_project is not None:
        result["registered_project"] = dict(registered_project)
    return result


def _blocked_result(reason: str) -> dict[str, Any]:
    return {
        "schema": PUBLIC_SAFE_SCHEMA_VERSION,
        "status": "BLOCKED",
        "reason": reason,
        "public_safe": True,
        "credentials_exposed": False,
        "local_paths_exposed": False,
        "cloud_codegen_enabled": False,
        "runtime_sync": False,
        "direct_main_mutation": False,
    }


def _bootstrap_blocked(reason: str, repository: str) -> dict[str, Any]:
    return {
        "schema": BOOTSTRAP_CONTRACT_VERSION,
        "status": "BLOCKED",
        "reason": reason,
        "repository": repository,
        "writable_repository": repository,
        "upstream_mutation_allowed": False,
        "direct_project_tree_mutation": False,
    }


def _redact_private_proposal(proposal: Mapping[str, Any], private: bool) -> dict[str, Any]:
    copied = copy.deepcopy(dict(proposal))
    if not private:
        return copied
    entry = copied.get("project_tree_entry")
    if isinstance(entry, dict):
        entry["checkout_path"] = PRIVATE_PATH_REDACTION
        entry["worktree_root"] = PRIVATE_PATH_REDACTION
    copied["private_local_paths_redacted"] = True
    return copied


def _public_project(project: Mapping[str, Any], private: bool) -> dict[str, Any]:
    public_project = copy.deepcopy(dict(project))
    if private:
        public_project["checkout_path"] = PRIVATE_PATH_REDACTION
        public_project["worktree_root"] = PRIVATE_PATH_REDACTION
    return public_project


def _stable_hash(value: Mapping[str, Any]) -> str:
    import json

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
