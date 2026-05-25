from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_required_commands_exist() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    for command in ["прокинься", "СК", "СК-свіжість", "ДЖ", "АУД", "КОД", "БЗ", "зафіксуй", "фіксуй", "+"]:
        assert command in commands


def test_command_modes_exist() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]
    modes = load_yaml("MODES.yaml")["modes"]

    for command, spec in commands.items():
        assert spec["mode"] in modes, command


def test_commands_have_typed_loads_produces_and_writes() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    for command, spec in commands.items():
        assert spec["loads"], command
        assert spec["produces"], command
        assert isinstance(spec["writes"], str), command


def test_boot_command_produces_boot_report_and_writes_none() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    assert commands["прокинься"]["produces"] == ["BootReport"]
    assert commands["прокинься"]["writes"] == "none"


def test_write_command_has_required_gates() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]
    gates = set(commands["БЗ"]["gates"])

    assert gates == {
        "read_target",
        "critique",
        "patch_plan",
        "explicit_approval",
        "write",
        "verify",
    }


def test_continue_command_is_bounded() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    assert commands["+"]["mode"] == "continue_approved_step"
    assert commands["+"]["rule"] == "apply_smallest_approved_next_step"


def test_fixuy_commands_record_without_extra_plus_but_keep_risky_actions_gated() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]

    for command in ["зафіксуй", "фіксуй"]:
        spec = commands[command]
        means = set(spec["means"])

        assert spec["mode"] == "persist_request"
        assert spec["writes"] == "routed_durable_storage_after_write_gate"
        assert "fixuy_is_enough_approval_to_record_stated_rule_after_classification" in means
        assert "route_public_safe_durable_to_GitHub_canon_review_without_extra_plus" in means
        assert "route_private_context_privately" in means
        assert "secrets_never_to_chat_GitHub_plain_Drive" in means
        assert "risky_actions_merge_deploy_runtime_secrets_destructive_require_separate_explicit_approval" in means
        assert (
            "report_to_Oleksii_human_readable_clear_person_to_person_technical_details_only_when_needed_or_requested"
            in means
        )


def test_behavior_playbook_consolidates_safe_helper_issue_first_and_repeated_work() -> None:
    commands = load_yaml("COMMANDS.yaml")
    playbook = commands["behavior_playbook"]

    assert playbook["routine_safe_helper_steps"] == {
        "trigger": "approved_helper_step_is_same_scope_read_only_or_current_scope_only_and_not_risky",
        "safe_next_step": "run_smallest_safe_helper_step_without_extra_plus",
        "report": "short_human_readable_status_result_next_safe_step",
    }
    assert playbook["blocked_long_task_creation"] == {
        "trigger": "long_task_creation_blocked_by_missing_detail_size_or_unsafe_ambiguity",
        "safe_next_step": "create_short_public_safe_issue_for_first_unblocker",
        "report": "short_human_readable_issue_reference_blocker_next_safe_step",
    }
    assert playbook["repeated_work_pattern"] == {
        "trigger": "same_type_routine_items_share_approved_route_scope_risk_level_and_gate",
        "safe_next_step": "process_as_batch_split_and_stop_on_different_or_risky_item",
        "report": "short_human_readable_processed_split_blocked_validation_summary",
    }
    assert playbook["skeleton_freshness_check"] == {
        "trigger": "before_skeleton_project_work_or_after_recent_main_merge",
        "safe_next_step": "compare_github_main_runner_checkout_sourcepack_and_open_work",
        "report": "short_human_readable_fresh_stale_blocked_summary",
    }


def test_batch_command_reports_repeated_work_without_weakening_gates() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]
    means = set(commands["пакетно"]["means"])

    assert commands["пакетно"]["rule"] == "same_type_routine_tasks_only_same_approved_route_scope_risk_gate"
    assert "require_same_approved_route_scope_risk_level_and_gate" in means
    assert "split_and_stop_if_any_item_differs" in means
    assert "report_processed_split_blocked_validation_summary" in means
    assert "no_batch_merge_deploy_secrets_runtime_canon_instruction_promotion_unless_explicitly_approved" in means


def test_skeleton_freshness_command_is_report_only_and_checks_canon_sources() -> None:
    commands = load_yaml("COMMANDS.yaml")["commands"]
    spec = commands["СК-свіжість"]
    means = set(spec["means"])

    assert spec["mode"] == "audit"
    assert spec["project"] == "skeleton"
    assert spec["writes"] == "none"
    assert spec["produces"] == ["SkeletonFreshnessReport"]
    assert "GitHub_main_status" in spec["loads"]
    assert "Runner_checkout_status" in spec["loads"]
    assert "open_PRs_and_issues" in spec["loads"]
    assert "GitHub_main_is_source_of_truth" in means
    assert "check_live_runner_checkout_sync_after_recent_merges" in means
    assert "check_notebooklm_sourcepack_freshness_when_relevant" in means
    assert "flag_open_prs_or_issues_stale_relative_to_main" in means
    assert "old_chats_and_old_branches_are_not_canon" in means
    assert "keep_report_short_human_readable" in means
