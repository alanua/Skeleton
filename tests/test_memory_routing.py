from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_source_registry_uses_explicit_override_chain() -> None:
    registry = load_yaml("SOURCE_REGISTRY.yaml")

    assert registry["source_override_chain"] == [
        "current_user_message",
        "boot_manifest",
        "public_github_canon",
        "private_memory",
        "chatgpt_memory",
        "archive_history_recovery",
    ]

    for source in registry["sources"].values():
        assert "priority" not in source


def test_source_registry_trust_levels_exist() -> None:
    sources = load_yaml("SOURCE_REGISTRY.yaml")["sources"]

    expected = {
        "current_user_message": "runtime_direct",
        "boot_manifest": "canon_route",
        "public_github_canon": "public_safe_canon_after_review",
        "private_memory": "private_working_memory",
        "chatgpt_memory": "weak_cache",
        "archive_history_recovery": "evidence_on_demand",
    }

    for source, trust in expected.items():
        assert sources[source]["trust"] == trust


def test_source_registry_has_conflict_rule() -> None:
    registry = load_yaml("SOURCE_REGISTRY.yaml")

    assert "conflict_rule" in registry
    assert "compare_conflicting_sources_by_override_chain" in registry["conflict_rule"]["means"]
    assert "use_boot_manifest_for_route_truth" in registry["conflict_rule"]["means"]


def test_memory_routes_exist() -> None:
    routes = load_yaml("MEMORY_ROUTING.yaml")["routes"]

    for route in [
        "public_safe_durable",
        "private_context",
        "secrets_credentials",
        "temporary_noise",
        "archive_evidence",
    ]:
        assert route in routes


def test_memory_routing_has_conflict_and_stale_rules() -> None:
    routing = load_yaml("MEMORY_ROUTING.yaml")

    assert "conflict_rule" in routing
    assert "stale_rule" in routing
    assert "use_SOURCE_REGISTRY_source_override_chain" in routing["conflict_rule"]["means"]
    assert "require_last_verified_for_state_files" in routing["stale_rule"]["means"]


def test_secrets_route_uses_secret_manager_target() -> None:
    routes = load_yaml("MEMORY_ROUTING.yaml")["routes"]

    assert routes["secrets_credentials"]["target"] == "local_encrypted_store_or_secret_manager"


def test_fixuy_memory_routes_are_explicit_and_secrets_refuse_plain_storage() -> None:
    routing = load_yaml("MEMORY_ROUTING.yaml")
    routes = routing["routes"]

    assert routes["public_safe_durable"]["fixuy_rule"] == (
        "record_after_classification_via_GitHub_canon_review_without_extra_plus"
    )
    assert routes["private_context"]["fixuy_rule"] == "record_after_classification_in_private_memory"
    assert routes["secrets_credentials"]["fixuy_rule"] == (
        "refuse_chat_GitHub_plain_storage_require_secret_manager_or_local_encrypted_route"
    )
    assert "secrets_credentials" in routing["classification_required_for"]


def test_fixuy_runtime_rule_is_not_chat_only() -> None:
    means = set(load_yaml("MEMORY_ROUTING.yaml")["fixuy_runtime_rule"]["means"])

    assert "fixuy_is_memory_routing_event" in means
    assert "classify_then_route_then_change_next_action" in means
    assert "fixation_without_changed_next_action_does_not_count" in means
    assert "use_memory_manager_and_memory_store_stage1_model_when_available" in means
    assert "report_classification_route_done_next_practical_step" in means


def test_risky_actions_still_require_separate_approval() -> None:
    routing = load_yaml("MEMORY_ROUTING.yaml")

    assert routing["risky_action_rule"]["still_requires_separate_explicit_approval"] == [
        "merge",
        "deploy",
        "runtime",
        "secrets",
        "destructive_operations",
    ]


def test_role_boundaries_exist() -> None:
    roles = load_yaml("SOURCE_REGISTRY.yaml")["roles"]

    for role in ["chatgpt", "skeleton", "runner", "codex", "gemini", "jeeves"]:
        assert role in roles
        assert "means" in roles[role]


def test_behavior_playbook_memory_routing_has_clear_trigger_step_and_report() -> None:
    playbook = load_yaml("MEMORY_ROUTING.yaml")["behavior_playbook"]

    assert set(playbook) == {
        "routine_safe_helper_steps",
        "blocked_long_task_creation",
        "repeated_work_pattern",
        "adaptive_practical_learning",
    }

    for rule in playbook.values():
        assert set(rule) == {"trigger", "safe_next_step", "report"}

    assert playbook["routine_safe_helper_steps"]["safe_next_step"] == (
        "run_smallest_safe_helper_step_without_extra_plus"
    )
    assert playbook["blocked_long_task_creation"]["safe_next_step"] == (
        "create_short_public_safe_issue_for_first_unblocker"
    )
    assert playbook["repeated_work_pattern"]["safe_next_step"] == (
        "process_as_batch_split_and_stop_on_different_or_risky_item"
    )
    assert playbook["adaptive_practical_learning"] == {
        "trigger": "repeated_blocker_or_repeated_failed_route_or_fixuy_with_adapt_instruction",
        "safe_next_step": "stop_repeating_failed_route_classify_ACTIVE_BLOCKER_repair_real_route_then_resume",
        "report": "classification_route_done_next_practical_step",
    }
