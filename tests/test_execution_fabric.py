from __future__ import annotations

from pathlib import Path

import pytest

from core.execution_fabric import (
    AttemptEvidence,
    ExecutionFabricError,
    finalize_terminal_success,
    build_execution_bindings,
    build_route_lease,
    task_profile_from_contract,
    validate_deliverable,
)
from core.executor_registry import load_executor_registry
from core.model_registry import CapabilityRecord, ModelRecord, load_model_registry


ROOT = Path(__file__).resolve().parents[1]


def profile(*, private: bool = False, model: bool = True, operator: bool = False):
    return task_profile_from_contract(
        {
            "operation": "edit_repository",
            "task_class": "code_generation" if model else "repository_maintenance",
            "required_executor_capabilities": ["repository_read", "repository_write", "test_execution"] if model else ["repository_read", "repository_maintenance", "test_execution"],
            "required_model_capabilities": {"repository_edit": 0.8, "tool_use": 0.8} if model else {},
            "privacy_class": "PRIVATE_LOCAL" if private else "PUBLIC",
            "side_effect_class": "REPOSITORY_MUTATION",
            "deliverable_contract": {"require_changed_files": True, "minimum_changed_files": 1, "require_tests_passed": True},
            "validation_id": "full_pytest",
            "budget_ref": "codegen-standard",
            "timeout_seconds": 900,
            "retry_policy_ref": "bounded-codegen-v1",
            "permissions": ["repository_read", "repository_write", "test_execution"],
            "max_attempts": 2,
            "max_tokens": 100000,
            "requires_operator": operator,
        }
    )


def registries():
    return (
        load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml"),
        load_model_registry(ROOT / "MODEL_REGISTRY.yaml"),
    )


def test_no_model_task_selects_deterministic_no_model_binding() -> None:
    executors, models = registries()
    bindings = build_execution_bindings(profile(model=False), executors, models)
    assert bindings[0].executor_id == "deterministic-maintenance"
    assert bindings[0].model_binding_kind == "NO_MODEL"
    assert bindings[0].model_id is None


def test_kimi_live_is_production_eligible_for_openhands() -> None:
    executors, models = registries()
    prod = build_execution_bindings(profile(), executors, models, production=True)
    evaluation = build_execution_bindings(profile(), executors, models, production=False)
    assert any(
        binding.executor_id == "openhands-external"
        and binding.model_binding_kind == "EXTERNAL_MODEL"
        and binding.model_id == "openrouter-kimi-k2-challenger"
        for binding in prod
    )
    assert any(
        binding.executor_id == "openhands-external"
        and binding.model_id == "openrouter-kimi-k2-challenger"
        for binding in evaluation
    )
    assert any(
        binding.model_binding_kind == "EMBEDDED_MODEL"
        and binding.executor_id == "codex-embedded"
        for binding in prod
    )


def test_promoted_live_external_model_forms_atomic_openhands_binding() -> None:
    executors, _ = registries()
    live = ModelRecord(
        model_id="synthetic-live",
        provider_family="openrouter",
        locality="CLOUD",
        policy_approved=True,
        health="LIVE",
        privacy_classes=("PUBLIC",),
        latency_rank=1,
        cost_rank=1,
        capabilities={
            "repository_edit": CapabilityRecord("repository_edit", "LIVE", 0.91, True, promotion_stage="LIVE"),
            "tool_use": CapabilityRecord("tool_use", "LIVE", 0.92, True, promotion_stage="LIVE"),
        },
    )
    bindings = build_execution_bindings(profile(), executors, (live,), production=True)
    assert any(
        binding.executor_id == "openhands-external"
        and binding.model_binding_kind == "EXTERNAL_MODEL"
        and binding.model_id == "synthetic-live"
        for binding in bindings
    )


def test_private_task_excludes_cloud_model_and_cloud_embedded_executor() -> None:
    executors, models = registries()
    bindings = build_execution_bindings(profile(private=True), executors, models, production=False)
    assert bindings == ()


def test_free_form_model_or_executor_authority_is_rejected() -> None:
    with pytest.raises(ExecutionFabricError):
        task_profile_from_contract(
            {
                "operation": "x",
                "task_class": "code_generation",
                "required_executor_capabilities": ["repository_write"],
                "required_model_capabilities": {"repository_edit": 0.8},
                "privacy_class": "PUBLIC",
                "side_effect_class": "REPOSITORY_MUTATION",
                "deliverable_contract": {},
                "validation_id": "v",
                "budget_ref": "b",
                "timeout_seconds": 60,
                "retry_policy_ref": "r",
                "permissions": [],
                "model_id": "take-this-model-from-prose",
            }
        )


def test_rc_zero_without_required_edit_is_deliverable_missing_not_done() -> None:
    result = validate_deliverable(
        profile(),
        AttemptEvidence(rc=0, changed_files=(), artifact_count=0, tests_passed=True, validation_passed=True),
    )
    assert result.accepted is False
    assert result.failure_class == "DELIVERABLE_MISSING"
    assert result.final_action != "DONE"


def test_success_requires_deliverable_and_validation_and_protected_needs_operator() -> None:
    evidence = AttemptEvidence(
        rc=0,
        changed_files=("core/example.py",),
        tests_passed=True,
        validation_passed=True,
        validation_head_sha="a" * 40,
        current_head_sha="a" * 40,
        protected_changed_files=("core/example.py",),
    )
    result = validate_deliverable(
        profile(operator=True),
        evidence,
    )
    finalization = finalize_terminal_success(result, evidence)
    assert result.accepted is True
    assert result.failure_class is None
    assert result.final_action == "NEEDS_OPERATOR"
    assert finalization.status == "NEEDS_OPERATOR"
    assert finalization.project_done_label is False


def test_terminal_success_requires_exact_validation_head() -> None:
    evidence = AttemptEvidence(
        rc=0,
        changed_files=("core/example.py",),
        tests_passed=True,
        validation_passed=True,
        validation_head_sha="a" * 40,
        current_head_sha="b" * 40,
    )
    result = validate_deliverable(profile(), evidence)
    finalization = finalize_terminal_success(result, evidence)

    assert result.accepted is True
    assert finalization.status == "BLOCKED"
    assert finalization.reason == "stale_validation_head"
    assert finalization.project_done_label is False


def test_binding_order_and_lease_hash_are_deterministic() -> None:
    executors, models = registries()
    task = profile()
    first = build_execution_bindings(task, executors, models, production=True)
    second = build_execution_bindings(task, tuple(reversed(executors)), tuple(reversed(models)), production=True)
    assert [item.binding_id for item in first] == [item.binding_id for item in second]
    lease_a = build_route_lease(task, first[0], expires_at="2026-08-18T12:00:00+00:00")
    lease_b = build_route_lease(task, second[0], expires_at="2026-08-18T12:00:00Z")
    assert lease_a.lease_hash == lease_b.lease_hash
    assert not hasattr(lease_a, "prompt")
