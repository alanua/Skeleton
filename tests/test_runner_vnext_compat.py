from __future__ import annotations

import pytest

from core.runner_vnext_compat import COMPATIBILITY_DELETION_CONDITION, CompatError, LegacyTaskObservation, adapt_legacy_task
from core.runner_vnext_contracts import EffectClass, PrivacyClass


def obs(**overrides) -> LegacyTaskObservation:
    values = dict(
        source_task_ref="github:issue-1",
        target_state_ref="git:abc",
        repo="alanua/Skeleton",
        branch="runner/test",
        task_kind="code_edit",
        requested_capabilities=("repository_read","repository_write_allowlisted"),
        allowed_files=("core/example.py",),
        privacy_boundary="PUBLIC_SAFE_REPOSITORY_ONLY",
        legacy_effect_class=EffectClass.GREEN,
        legacy_status="runner:ready",
        evidence_refs=("github:comment-1",),
    )
    values.update(overrides)
    return LegacyTaskObservation(**values)


def test_code_edit_maps_one_way_to_typed_vnext_plan() -> None:
    adapted = adapt_legacy_task(obs())
    assert adapted.operation.kind == "workspace_write"
    assert adapted.operation.effects == ("workspace_write",)
    assert adapted.task.privacy is PrivacyClass.PUBLIC_SAFE
    assert adapted.policy_input.target_state_ref == "git:abc"


def test_protected_path_is_not_disguised_as_ordinary_repo_resource() -> None:
    adapted = adapt_legacy_task(obs(allowed_files=("scripts/runner_poll_github_tasks.py",)))
    assert adapted.operation.resources == ("protected:scripts/runner_poll_github_tasks.py",)


def test_unsupported_or_ambiguous_legacy_task_fails_closed() -> None:
    with pytest.raises(CompatError, match="LEGACY_TASK_KIND_UNSUPPORTED"):
        adapt_legacy_task(obs(task_kind="loop_control"))


def test_private_memory_requires_private_boundary() -> None:
    with pytest.raises(CompatError, match="LEGACY_PRIVATE_TASK_PRIVACY_MISMATCH"):
        adapt_legacy_task(obs(task_kind="private_memory"))
    adapted = adapt_legacy_task(obs(task_kind="private_memory", privacy_boundary="PRIVATE_MEMORY"))
    assert adapted.task.privacy is PrivacyClass.PRIVATE


def test_private_or_unsafe_paths_and_refs_fail_closed() -> None:
    with pytest.raises(CompatError, match="LEGACY_REPOSITORY_PATH_INVALID"):
        adapt_legacy_task(obs(allowed_files=("/private/file",)))
    with pytest.raises(CompatError, match="PUBLIC_SAFE_REF_REQUIRED"):
        adapt_legacy_task(obs(source_task_ref="/private/task"))



def test_compatibility_adapter_has_explicit_deletion_condition() -> None:
    assert "Delete this adapter" in COMPATIBILITY_DELETION_CONDITION
    assert "operator-approved vNext cutover" in COMPATIBILITY_DELETION_CONDITION
