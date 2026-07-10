from __future__ import annotations

import inspect

import pytest

from core import runner_shadow_integration as shadow
from core.runner_executor_registry import RunnerExecutorRegistry
from core.runner_shadow_integration import (
    LEGACY_ROUTE_CODE_GENERATION,
    LEGACY_ROUTE_PUBLISH_ONLY,
    LEGACY_ROUTE_RUNTIME_ONLY,
    NON_MERGE_PUBLISH_REASON,
    ROUTE_TO_REQUIRED_CAPABILITIES,
    SIGNED_MERGE_MODE,
    RunnerShadowCompatibilityBindings,
    build_shadow_executor_registry,
    evaluate_shadow_from_normalized_metadata,
    registry_parity_reason_codes,
    runner_task_from_normalized_metadata,
    task_envelope_hash,
)


BASE_SHA = "b" * 40
HEAD_SHA = "c" * 40
APPROVAL = "operator-approval-1683"


def metadata(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "issue_number": 1683,
        "legacy_route": LEGACY_ROUTE_CODE_GENERATION,
        "repo": "alanua/Skeleton",
        "branch": "runner/issue-1683",
        "base_sha": BASE_SHA,
        "allowed_files": ("tests/test_runner_shadow_integration.py",),
        "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
        "approval_reference": APPROVAL,
        "trusted_approval_references": (APPROVAL,),
        "trusted_protected_approval_references": (),
        "idempotency_key": "issue-1683-shadow-parity",
        "validation_timeout_seconds": 900,
    }
    value.update(updates)
    return value


def test_real_structured_fields_build_deterministic_task_hash() -> None:
    first = metadata(allowed_files=("tests/test_runner_shadow_integration.py",))
    second = metadata(allowed_files=["tests/test_runner_shadow_integration.py"])

    first_task = runner_task_from_normalized_metadata(first)
    second_task = runner_task_from_normalized_metadata(second)

    assert first_task.to_json() == second_task.to_json()
    assert task_envelope_hash(first_task) == task_envelope_hash(second_task)
    assert (
        evaluate_shadow_from_normalized_metadata(first).task_envelope_hash
        == evaluate_shadow_from_normalized_metadata(second).task_envelope_hash
    )


@pytest.mark.parametrize(
    ("updates", "expected_route"),
    (
        ({}, "code_edit"),
        (
            {
                "legacy_route": LEGACY_ROUTE_RUNTIME_ONLY,
                "maintenance_task_id": "check_skeleton_freshness",
            },
            "repository_maintenance",
        ),
        (
            {
                "legacy_route": LEGACY_ROUTE_RUNTIME_ONLY,
                "maintenance_task_id": "hermes_memory_gateway_smoke",
                "privacy_boundary": "LOCAL_PRIVATE",
            },
            "private_memory",
        ),
        (
            {
                "legacy_route": LEGACY_ROUTE_RUNTIME_ONLY,
                "maintenance_task_id": "mempalace_synthetic_runtime_smoke",
                "privacy_boundary": "PUBLIC_SAFE_AGGREGATE_ONLY",
            },
            "diagnostic",
        ),
        (
            {
                "legacy_route": LEGACY_ROUTE_RUNTIME_ONLY,
                "maintenance_task_id": "loop_engine_packet",
                "privacy_boundary": "PUBLIC_SAFE_AGGREGATE_ONLY",
            },
            "loop_control",
        ),
        (
            {
                "legacy_route": LEGACY_ROUTE_PUBLISH_ONLY,
                "publish_mode": SIGNED_MERGE_MODE,
                "allowed_files": ("docs/RUNNER_QUEUE_STATUS.md",),
                "pr_number": 17,
                "current_head_sha": HEAD_SHA,
            },
            "publish",
        ),
    ),
)
def test_compatibility_registry_verifies_all_six_routes_without_calling_handlers(
    updates: dict[str, object],
    expected_route: str,
) -> None:
    receipt = evaluate_shadow_from_normalized_metadata(metadata(**updates))

    assert receipt.shadow_status == "allowed"
    assert receipt.semantic_route == expected_route
    registry = build_shadow_executor_registry()
    assert registry.registered_task_kinds == (
        "code_edit",
        "diagnostic",
        "loop_control",
        "private_memory",
        "publish",
        "repository_maintenance",
    )
    for task_kind in registry.registered_task_kinds:
        assert registry.lookup(task_kind).required_capabilities == tuple(
            sorted(ROUTE_TO_REQUIRED_CAPABILITIES[task_kind])
        )


def test_forged_protected_approval_reference_is_blocked() -> None:
    receipt = evaluate_shadow_from_normalized_metadata(
        metadata(
            allowed_files=("scripts/runner_poll_github_tasks.py",),
            approval_reference="forged-protected-approval",
            trusted_approval_references=("forged-protected-approval",),
            trusted_protected_approval_references=(),
        )
    )

    assert receipt.shadow_status == "blocked"
    assert "PROTECTED_RESOURCE_APPROVAL_MISSING" in receipt.reason_codes


def test_independently_trusted_protected_approval_is_accepted() -> None:
    receipt = evaluate_shadow_from_normalized_metadata(
        metadata(
            allowed_files=("scripts/runner_poll_github_tasks.py",),
            trusted_protected_approval_references=(APPROVAL,),
        )
    )

    assert receipt.shadow_status == "allowed"


def test_arbitrary_task_text_and_private_values_never_appear_in_receipt() -> None:
    private_text = "PRIVATE_TOKEN=super-secret-local-path-/home/operator/private"
    receipt = evaluate_shadow_from_normalized_metadata(
        metadata(expected_output=(private_text,), arbitrary_payload=private_text)
    )
    public_text = repr(receipt.to_public_mapping())

    assert set(receipt.to_public_mapping()) == set(shadow.RECEIPT_KEYS)
    assert receipt.task_envelope_hash is not None
    assert private_text not in public_text
    assert "/home/operator" not in public_text


@pytest.mark.parametrize(
    "task_id",
    (
        "inspect_issue_worktree_for_publish",
        "publish_issue_worktree_pr",
        "publish_existing_issue_worktree",
        "publish_issue_worktree_to_existing_pr",
        "overlay_registered_worktree_to_existing_pr",
        "publish_target_project_issue_worktree_pr",
        "publish_container_validation_worktree",
    ),
)
def test_non_merge_publish_routes_are_not_evaluated_as_merge_actions(task_id: str) -> None:
    receipt = evaluate_shadow_from_normalized_metadata(
        metadata(legacy_route=LEGACY_ROUTE_RUNTIME_ONLY, maintenance_task_id=task_id)
    )

    assert receipt.shadow_status == "not_applicable"
    assert receipt.reason_codes == (NON_MERGE_PUBLISH_REASON,)


def test_signed_merge_mode_uses_exact_pr_head_files_approval_parity() -> None:
    blocked = evaluate_shadow_from_normalized_metadata(
        metadata(
            legacy_route=LEGACY_ROUTE_PUBLISH_ONLY,
            publish_mode=SIGNED_MERGE_MODE,
            allowed_files=("docs/RUNNER_QUEUE_STATUS.md",),
            trusted_approval_references=(),
            pr_number=17,
            current_head_sha=HEAD_SHA,
        )
    )
    allowed = evaluate_shadow_from_normalized_metadata(
        metadata(
            legacy_route=LEGACY_ROUTE_PUBLISH_ONLY,
            publish_mode=SIGNED_MERGE_MODE,
            allowed_files=("docs/RUNNER_QUEUE_STATUS.md",),
            pr_number=17,
            current_head_sha=HEAD_SHA,
        )
    )

    assert "APPROVAL_REFERENCE_NOT_APPROVED" in blocked.reason_codes
    assert "ACTION_GATE_BLOCKED" in blocked.reason_codes
    assert allowed.shadow_status == "allowed"


def test_capability_mismatch_fails_closed_without_dispatch() -> None:
    task = runner_task_from_normalized_metadata(
        metadata(requested_capabilities=("repository_read",))
    )
    registry = RunnerExecutorRegistry([])

    assert registry_parity_reason_codes(registry, task) == (
        "REGISTRY_ROUTE_MISMATCH",
        "EXECUTOR_NOT_REGISTERED",
    )


def test_removed_real_legacy_maintenance_binding_fails_parity() -> None:
    compatibility = RunnerShadowCompatibilityBindings(
        legacy_routes=shadow.DEFAULT_COMPATIBILITY_BINDINGS.legacy_routes,
        route_task_kind_by_route=shadow.DEFAULT_COMPATIBILITY_BINDINGS.route_task_kind_by_route,
        maintenance_task_kind_by_id={
            task_id: task_kind
            for task_id, task_kind in shadow.DEFAULT_COMPATIBILITY_BINDINGS.maintenance_task_kind_by_id.items()
            if task_id != "check_skeleton_freshness"
        },
        publish_maintenance_task_ids=shadow.DEFAULT_COMPATIBILITY_BINDINGS.publish_maintenance_task_ids,
    )

    receipt = evaluate_shadow_from_normalized_metadata(
        metadata(
            legacy_route=LEGACY_ROUTE_RUNTIME_ONLY,
            maintenance_task_id="check_skeleton_freshness",
        ),
        compatibility,
    )

    assert receipt.shadow_status == "blocked"
    assert receipt.reason_codes == ("LEGACY_MAINTENANCE_BINDING_MISSING",)


def test_no_runner_lease_store_method_is_reachable_from_shadow_evaluation() -> None:
    source = inspect.getsource(shadow)

    assert "RunnerLeaseStore" not in source
    assert "runner_lease_store" not in source
