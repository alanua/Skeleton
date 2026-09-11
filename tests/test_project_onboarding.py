from __future__ import annotations

import pytest

from core.home_edge.project_onboarding import (
    ProjectOnboardingBackend,
    ProjectOnboardingCode,
    ProjectOnboardingError,
    RepoIdentity,
    RepositoryKind,
    RepositorySnapshot,
)


def test_canonical_backend_enforces_empty_bootstrap_and_fork_upstream_invariants() -> None:
    empty = RepoIdentity("github.com", "acme", "Empty")
    fork = RepoIdentity("github.com", "acme", "Fork")
    backend = ProjectOnboardingBackend(
        repositories={
            empty.canonical_url: RepositorySnapshot(identity=empty, kind=RepositoryKind.EMPTY, empty=True),
            fork.canonical_url: RepositorySnapshot(
                identity=fork,
                kind=RepositoryKind.FORK,
                fork=True,
                upstream=RepoIdentity("github.com", "alanua", "Skeleton"),
            ),
        }
    )

    with pytest.raises(ProjectOnboardingError) as empty_exc:
        backend.register(repo_url=empty.canonical_url, project_tree_approved=True)
    assert empty_exc.value.code == ProjectOnboardingCode.BOOTSTRAP_REQUIRED

    with pytest.raises(ProjectOnboardingError) as fork_exc:
        backend.prepare_register(repo_url=fork.canonical_url, mutate_upstream=True)
    assert fork_exc.value.code == ProjectOnboardingCode.ACCESS_DENIED


def test_canonical_backend_never_claims_active_before_runtime_ready() -> None:
    backend = ProjectOnboardingBackend()
    result = backend.register(repo_url="https://github.com/acme/Standard", project_tree_approved=True)
    assert result["code"] == "APPROVAL_REQUIRED"
    assert result["registration"]["status"] == "NEEDS_APPROVAL"
    assert "protected" in result
