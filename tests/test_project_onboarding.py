from __future__ import annotations

import copy

import pytest

from core.project_onboarding import (
    BOOTSTRAP_ACTION,
    inspect_project_onboarding,
    validate_empty_bootstrap_packet,
    validate_mutation_target,
)
from core.project_tree import load_project_tree
from scripts import runner_poll_github_tasks as runner


HEAD_SHA = "a" * 40


def project_tree() -> dict:
    return load_project_tree("PROJECT_TREE.yaml")


def standard_metadata(**updates: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "full_name": "alanua/NewApp",
        "private": False,
        "fork": False,
        "default_branch": "main",
        "head_sha": HEAD_SHA,
        "size": 12,
    }
    metadata.update(updates)
    if "html_url" not in updates:
        metadata["html_url"] = f"https://github.com/{metadata['full_name']}"
    return metadata


def empty_metadata(**updates: object) -> dict[str, object]:
    metadata = standard_metadata(
        full_name="alanua/EmptyApp",
        default_branch=None,
        head_sha=None,
        size=0,
        empty=True,
    )
    metadata.update(updates)
    return metadata


def fork_metadata(**updates: object) -> dict[str, object]:
    metadata = standard_metadata(
        full_name="alanua/ForkApp",
        fork=True,
        parent={
            "full_name": "upstream/ForkApp",
            "html_url": "https://github.com/upstream/ForkApp",
            "private": False,
        },
    )
    metadata.update(updates)
    return metadata


def test_empty_repo_metadata_requires_explicit_bootstrap() -> None:
    result = inspect_project_onboarding(empty_metadata(), project_tree())

    assert result["status"] == "NEEDS_BOOTSTRAP"
    assert result["classification"] == "EMPTY"
    assert result["protected_pr_required"] is False
    assert result["direct_main_mutation"] is False
    assert result["bootstrap_contract"] == {
        "schema": "project_onboarding.empty_bootstrap.v1",
        "repository": "alanua/EmptyApp",
        "action": BOOTSTRAP_ACTION,
        "branch": "main",
        "initial_commit_required": True,
        "allowed_files": [".gitignore", "README.md"],
        "required_files": ["README.md"],
        "registration_after_bootstrap": "protected_draft_pr",
    }


def test_explicit_empty_bootstrap_packet_validates_exact_minimal_shape() -> None:
    result = validate_empty_bootstrap_packet(
        {
            "action": BOOTSTRAP_ACTION,
            "repository": "alanua/EmptyApp",
            "target_repository": "alanua/EmptyApp",
            "branch": "main",
            "initial_commit": True,
            "files": [{"path": "README.md"}, {"path": ".gitignore"}],
        },
        empty_metadata(),
    )

    assert result["status"] == "BOOTSTRAP_ALLOWED"
    assert result["allowed_files"] == [".gitignore", "README.md"]
    assert result["commit_shape"] == "initial_main_readme_optional_gitignore"


@pytest.mark.parametrize(
    ("packet_updates", "reason"),
    (
        ({"repository": "alanua/Other"}, "repository_mismatch"),
        ({"branch": "master"}, "bootstrap_branch_must_be_main"),
        ({"files": [{"path": "README.md"}, {"path": "src/app.py"}]}, "bootstrap_files_not_minimal"),
        ({"files": [{"path": "../README.md"}]}, "bootstrap_file_path_invalid"),
    ),
)
def test_empty_bootstrap_packet_blocks_non_exact_shapes(
    packet_updates: dict[str, object], reason: str
) -> None:
    packet: dict[str, object] = {
        "action": BOOTSTRAP_ACTION,
        "repository": "alanua/EmptyApp",
        "branch": "main",
        "initial_commit": True,
        "files": [{"path": "README.md"}],
    }
    packet.update(packet_updates)

    result = validate_empty_bootstrap_packet(packet, empty_metadata())

    assert result["status"] == "BLOCKED"
    assert result["reason"] == reason


def test_malformed_and_non_github_repository_identity_blocks() -> None:
    result = inspect_project_onboarding(
        standard_metadata(full_name="https://gitlab.com/alanua/NewApp"),
        project_tree(),
    )

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "canonical_github_repository_required"


def test_fork_metadata_captures_upstream_read_only_and_keeps_fork_writable() -> None:
    result = inspect_project_onboarding(fork_metadata(), project_tree())

    assert result["status"] == "PROPOSE_REGISTRATION"
    assert result["classification"] == "FORK"
    assert result["writable_repository"] == "alanua/ForkApp"
    assert result["fork_upstream"] == {
        "repository": "upstream/ForkApp",
        "read_only": True,
        "mutation_allowed": False,
    }
    proposal = result["registration_proposal"]
    assert proposal["writable_repository"] == "alanua/ForkApp"
    assert proposal["mutation_target_repository"] == "alanua/ForkApp"
    assert proposal["upstream_read_only"] is True


def test_attempt_to_target_fork_upstream_for_mutation_blocks() -> None:
    result = validate_mutation_target(fork_metadata(), "upstream/ForkApp")

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "upstream_mutation_blocked"
    assert result["writable_repository"] == "alanua/ForkApp"
    assert result["upstream_read_only"] is True


def test_standard_repo_proposes_exact_default_branch_head_registration() -> None:
    result = inspect_project_onboarding(standard_metadata(), project_tree())

    assert result["status"] == "PROPOSE_REGISTRATION"
    proposal = result["registration_proposal"]
    assert proposal["mode"] == "protected_draft_pr"
    assert proposal["protected_pr_required"] is True
    assert proposal["direct_main_mutation"] is False
    assert proposal["runtime_sync"] is False
    assert proposal["repository"] == "alanua/NewApp"
    assert proposal["default_branch"] == "main"
    assert proposal["head_sha"] == HEAD_SHA
    assert proposal["readiness_canary"] == {
        "schema": "project_onboarding.readiness_canary.v1",
        "repository": "alanua/NewApp",
        "default_branch": "main",
        "head_sha": HEAD_SHA,
        "mode": "read_only",
        "checks": (
            "registered_project_tree_entry_present",
            "github_default_branch_head_matches",
            "runner_queue_read_access_only",
        ),
        "mutation_allowed": False,
    }


def test_private_repo_public_receipt_redacts_paths_and_disables_cloud_codegen() -> None:
    result = inspect_project_onboarding(
        standard_metadata(full_name="alanua/PrivateApp", private=True),
        project_tree(),
    )

    assert result["status"] == "PROPOSE_REGISTRATION"
    assert result["private"] is True
    assert result["cloud_codegen_enabled"] is False
    assert result["local_paths_exposed"] is False
    entry = result["registration_proposal"]["project_tree_entry"]
    assert entry["public"] is False
    assert entry["runner_enabled"] is False
    assert entry["execution_modes"] == {
        "planning_only": True,
        "codex_issue_worktree": False,
        "live_cross_repo": False,
    }
    assert entry["checkout_path"] == "<redacted-private-runner-path>"
    assert entry["worktree_root"] == "<redacted-private-runner-path>"


def test_duplicate_exact_registration_is_idempotent_already_registered() -> None:
    result = inspect_project_onboarding(
        standard_metadata(full_name="alanua/Skeleton"),
        project_tree(),
    )

    assert result["status"] == "ALREADY_REGISTERED"
    assert result["project_id"] == "skeleton"
    assert result["reason"] == "exact_repository_already_registered"


@pytest.mark.parametrize(
    "metadata",
    (
        standard_metadata(full_name="alanua/Skeleton-Admin"),
        standard_metadata(full_name="alanua/NewApp", html_url="https://github.com/alanua/NewApp?token=secret"),
    ),
)
def test_project_onboarding_blocks_conflicts_and_credentials(metadata: dict[str, object]) -> None:
    tree = project_tree()
    if metadata["full_name"] == "alanua/Skeleton-Admin":
        conflict = copy.deepcopy(tree["projects"]["skeleton"])
        conflict["repo"] = "alanua/OtherSkeletonAdmin"
        conflict["checkout_path"] = "/home/agent/agent-dev/worktrees/alanua-skeleton-admin/main"
        conflict["worktree_root"] = "/home/agent/agent-dev/worktrees/alanua-skeleton-admin"
        conflict["worktree_name_prefix"] = "alanua-skeleton-admin"
        tree["projects"]["skeleton_admin"] = conflict

    result = inspect_project_onboarding(metadata, tree)

    assert result["status"] == "BLOCKED"


def test_runner_service_wrapper_returns_public_safe_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "load_runner_project_tree", project_tree)

    result = runner.inspect_runner_project_onboarding(standard_metadata())

    assert result["schema"] == "project_onboarding.public_result.v1"
    assert result["status"] == "PROPOSE_REGISTRATION"
    assert result["credentials_exposed"] is False
