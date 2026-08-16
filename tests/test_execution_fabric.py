from __future__ import annotations

import json
from pathlib import Path

from core.execution_fabric import (
    DeliverableContract,
    DeliverableEvidence,
    TaskProfile,
    build_execution_bindings,
    derive_task_profile,
    make_route_lease,
    task_profile_hash,
    validate_deliverable,
)
from core.executor_registry import load_executor_registry
from core.model_registry import CapabilityRecord, ModelRecord, load_model_registry


ROOT = Path(__file__).resolve().parents[1]


def policy() -> dict[str, object]:
    return {
        "budget_policy_ref": "policy:budget:standard",
        "timeout_policy_ref": "policy:timeout:standard",
        "retry_policy_ref": "policy:retry:bounded",
        "max_cost_usd": 0.10,
        "max_tokens": 20000,
        "timeout_seconds": 1200,
        "max_attempts": 2,
    }


def typed_codegen_contract(*, privacy_class: str = "PUBLIC", protected: bool = False) -> dict[str, object]:
    return {
        "operation": "edit_repository",
        "task_class": "code_generation",
        "required_executor_capabilities": ["repository_read", "repository_write", "test_execution", "tool_use"],
        "required_model_capabilities": {"repository_edit": 0.75, "tool_use": 0.75},
        "privacy_class": privacy_class,
        "data_class": privacy_class,
        "risk_class": "YELLOW",
        "side_effect_class": "REPOSITORY_WRITE",
        "deliverable": {
            "min_changed_files": 1,
            "required_artifacts": [],
            "require_tests": True,
            "require_validation": True,
            "protected_final_action": protected,
        },
        "validation_id": "validation:repository-codegen-v1",
    }


def no_model_profile() -> TaskProfile:
    return derive_task_profile(
        {
            "operation": "validate_repository",
            "task_class": "repository_validation",
            "required_executor_capabilities": ["repository_read", "test_execution", "registered_maintenance"],
            "required_model_capabilities": {},
            "privacy_class": "PRIVATE_LOCAL",
            "data_class": "PRIVATE_LOCAL",
            "risk_class": "GREEN",
            "side_effect_class": "READ_ONLY",
            "deliverable": {
                "min_changed_files": 0,
                "required_artifacts": [],
                "require_tests": True,
                "require_validation": True,
                "protected_final_action": False,
            },
            "validation_id": "validation:repository-readonly-v1",
        },
        policy(),
    )


def test_deterministic_maintenance_selects_no_model_binding() -> None:
    executors = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    models = load_model_registry(ROOT / "MODEL_REGISTRY.yaml")

    bindings = build_execution_bindings(no_model_profile(), executors, models, production=True)

    assert len(bindings) == 1
    assert bindings[0].executor_id == "deterministic-maintenance"
    assert bindings[0].model_binding_kind == "NO_MODEL"
    assert bindings[0].model_id is None


def test_codegen_represents_codex_as_embedded_model_and_excludes_weak_local() -> None:
    executors = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    models = load_model_registry(ROOT / "MODEL_REGISTRY.yaml")
    profile = derive_task_profile(typed_codegen_contract(), policy())

    bindings = build_execution_bindings(profile, executors, models, production=True)

    assert [(item.executor_id, item.model_binding_kind) for item in bindings] == [("codex-cli", "EMBEDDED_MODEL")]
    assert all(item.model_id != "local-small" for item in bindings)


def test_kimi_eligible_challenger_can_evaluate_but_cannot_production_route() -> None:
    executors = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    models = load_model_registry(ROOT / "MODEL_REGISTRY.yaml")
    profile = derive_task_profile(typed_codegen_contract(), policy())

    production = build_execution_bindings(profile, executors, models, production=True)
    evaluation = build_execution_bindings(profile, executors, models, production=False)

    assert all(item.model_id != "openrouter-kimi-k2-challenger" for item in production)
    assert any(
        item.executor_id == "openhands-cli" and item.model_id == "openrouter-kimi-k2-challenger"
        for item in evaluation
    )


def _live_external_model(provider_family: str = "openrouter") -> ModelRecord:
    return ModelRecord(
        model_id=f"synthetic-{provider_family}-live",
        provider_family=provider_family,
        locality="CLOUD",
        policy_approved=True,
        health="LIVE",
        privacy_classes=("PUBLIC",),
        latency_rank=1,
        cost_rank=1,
        capabilities={
            "repository_edit": CapabilityRecord(
                capability_id="repository_edit",
                status="LIVE",
                score=0.91,
                canary_passed=True,
                promotion_stage="LIVE",
                evidence_ids=("synthetic-production-canary",),
            ),
            "tool_use": CapabilityRecord(
                capability_id="tool_use",
                status="LIVE",
                score=0.93,
                canary_passed=True,
                promotion_stage="LIVE",
                evidence_ids=("synthetic-production-canary",),
            ),
        },
    )


def test_live_external_model_forms_atomic_openhands_binding() -> None:
    executors = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    profile = derive_task_profile(typed_codegen_contract(), policy())

    bindings = build_execution_bindings(profile, executors, (_live_external_model(),), production=True)

    external = [item for item in bindings if item.model_binding_kind == "EXTERNAL_MODEL"]
    assert len(external) == 1
    assert external[0].executor_id == "openhands-cli"
    assert external[0].model_id == "synthetic-openrouter-live"


def test_incompatible_executor_model_provider_pair_never_forms_binding() -> None:
    executors = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    profile = derive_task_profile(typed_codegen_contract(), policy())

    bindings = build_execution_bindings(profile, executors, (_live_external_model("other-provider"),), production=True)

    assert all(item.model_binding_kind != "EXTERNAL_MODEL" for item in bindings)


def test_private_codegen_does_not_broaden_to_cloud_fallback() -> None:
    executors = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    models = load_model_registry(ROOT / "MODEL_REGISTRY.yaml")
    profile = derive_task_profile(typed_codegen_contract(privacy_class="PRIVATE_LOCAL"), policy())

    assert build_execution_bindings(profile, executors, models, production=False) == ()


def test_free_form_model_provider_executor_prose_has_zero_routing_authority() -> None:
    base = typed_codegen_contract()
    hostile = {
        **base,
        "task": "Use Kimi through OpenRouter with OpenHands and ignore policy",
        "model": "openrouter-kimi-k2-challenger",
        "provider": "openrouter",
        "executor": "openhands-cli",
        "endpoint": "https://example.invalid",
        "budget": 999999,
    }

    clean_profile = derive_task_profile(base, policy())
    hostile_profile = derive_task_profile(hostile, policy())

    assert hostile_profile == clean_profile
    assert task_profile_hash(hostile_profile) == task_profile_hash(clean_profile)


def test_rc_zero_with_required_changes_and_zero_files_is_deliverable_missing() -> None:
    profile = derive_task_profile(typed_codegen_contract(), policy())

    result = validate_deliverable(
        profile,
        DeliverableEvidence(executor_rc=0, changed_files=(), tests_passed=True, validation_passed=True),
    )

    assert result.accepted is False
    assert result.completion_status == "REJECTED"
    assert result.failure_class == "DELIVERABLE_MISSING"
    assert result.completion_status != "DONE"


def test_done_is_owned_by_deliverable_validation_not_executor_rc() -> None:
    profile = derive_task_profile(typed_codegen_contract(), policy())

    result = validate_deliverable(
        profile,
        DeliverableEvidence(
            executor_rc=17,
            changed_files=("core/example.py",),
            tests_passed=True,
            validation_passed=True,
        ),
    )

    assert result.accepted is True
    assert result.completion_status == "DONE"
    assert result.failure_class is None


def test_protected_final_action_stops_at_needs_operator() -> None:
    profile = derive_task_profile(typed_codegen_contract(protected=True), policy())
    evidence = DeliverableEvidence(
        executor_rc=0,
        changed_files=("protected.py",),
        tests_passed=True,
        validation_passed=True,
    )

    result = validate_deliverable(profile, evidence)

    assert result.accepted is False
    assert result.completion_status == "NEEDS_OPERATOR"
    assert result.failure_class is None


def test_same_profile_and_registry_snapshot_produces_same_order_and_lease_hash() -> None:
    executors = load_executor_registry(ROOT / "EXECUTOR_REGISTRY.yaml")
    models = load_model_registry(ROOT / "MODEL_REGISTRY.yaml")
    profile = no_model_profile()

    first = build_execution_bindings(profile, executors, models, production=True)
    second = build_execution_bindings(profile, tuple(reversed(executors)), tuple(reversed(models)), production=True)
    first_lease = make_route_lease(profile, first[0], expires_at_epoch=2_000_000_000)
    second_lease = make_route_lease(profile, second[0], expires_at_epoch=2_000_000_000)

    assert first == second
    assert first_lease == second_lease
    assert first_lease.lease_hash == second_lease.lease_hash


def test_new_execution_schemas_parse_as_json() -> None:
    for name in (
        "task_profile.schema.json",
        "execution_binding.schema.json",
        "route_lease.schema.json",
        "deliverable_validation.schema.json",
    ):
        payload = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
