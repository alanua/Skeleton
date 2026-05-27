from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs" / "SKELETON_IMPROVEMENT_PROTOCOL.md"
REGISTRY = ROOT / "docs" / "SKELETON_IMPROVEMENT_PROTOCOL.yaml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def test_protocol_documents_exist() -> None:
    assert PROTOCOL.is_file()
    assert REGISTRY.is_file()


def test_protocol_records_controlled_loop() -> None:
    text = read(PROTOCOL).lower()
    registry = load_registry()

    expected_loop = [
        "observe",
        "detect",
        "classify",
        "propose",
        "critique",
        "approve",
        "execute",
        "validate",
        "merge",
        "sync",
        "monitor",
        "repair or rollback",
    ]

    for step in expected_loop:
        assert step in text

    assert registry["controlled_loop"] == [
        "observe",
        "detect",
        "classify",
        "propose",
        "critique",
        "approve",
        "execute",
        "validate",
        "merge",
        "sync",
        "monitor",
        "repair_or_rollback",
    ]


def test_protocol_records_improvement_categories() -> None:
    text = read(PROTOCOL)
    registry = load_registry()

    for category in [
        "Documentation/plan update",
        "Operating-rule update",
        "Task queue repair",
        "Publisher/delivery repair",
        "Worker selection update",
        "Adapter discovery",
        "Runtime-sensitive change",
        "Secrets-sensitive change",
        "Dashboard/status reporting",
        "Project handoff/state update",
    ]:
        assert category in text

    assert registry["improvement_categories"] == [
        "documentation_plan_update",
        "operating_rule_update",
        "task_queue_repair",
        "publisher_delivery_repair",
        "worker_selection_update",
        "adapter_discovery",
        "runtime_sensitive_change",
        "secrets_sensitive_change",
        "dashboard_status_reporting",
        "project_handoff_state_update",
    ]


def test_protocol_records_risk_levels_and_required_gates() -> None:
    text = read(PROTOCOL)
    registry = load_registry()

    for phrase in [
        "Green improvements are docs/tests only and contain no behavior change",
        "Yellow improvements change workflow or tooling without runtime, secrets",
        "Red improvements touch runtime, deploy, secrets, production database",
        "GitHub issue",
        "bounded scope",
        "allowed files",
        "validation before merge",
        "PR review",
        "explicit separate human approval",
    ]:
        assert phrase in text

    assert registry["risk_levels"]["green"]["definition"] == "docs_tests_only_no_behavior_change"
    assert registry["risk_levels"]["yellow"]["definition"] == (
        "workflow_tooling_changes_without_runtime_secrets_or_deploy"
    )
    assert registry["risk_levels"]["red"]["definition"] == (
        "runtime_deploy_secrets_production_db_service_restart_live_worker_or_merge_automation"
    )
    assert "github_issue" in registry["risk_levels"]["green"]["required_gates"]
    assert "critique_before_canon_rules_roadmap_boot_worker_or_approval_gate_change" in (
        registry["risk_levels"]["yellow"]["required_gates"]
    )
    assert "explicit_separate_human_approval_for_risky_action" in (
        registry["risk_levels"]["red"]["required_gates"]
    )


def test_protocol_records_boundaries_and_non_autonomy() -> None:
    text = read(PROTOCOL)
    registry = load_registry()

    for phrase in [
        "This is not autonomous self-modification",
        "Skeleton may propose improvements but must not apply them autonomously",
        "Every durable change requires a GitHub issue, bounded scope, allowed files",
        "Critique is required before changing canon, rules, roadmap, boot route",
        "`OPERATOR_RULES.yaml` stays a behavior and operating-rule registry",
        "not a runtime gate",
        "no Python gate code",
        "no Telegram callback change",
        "no merge automation",
    ]:
        assert phrase in text

    assert registry["enforcement_model"]["runtime_enforcement"] is False
    assert registry["enforcement_model"]["python_gate_module"] == "none"
    assert registry["enforcement_model"]["autonomous_self_modification"] is False
    assert registry["operator_rules_relationship"]["duplicates_operator_rules"] is False
    assert "no_autonomous_self_modification" in registry["required_boundaries"]
    assert "python_gate_code" in registry["forbidden_in_stage_1"]
    assert "telegram_callback_change" in registry["forbidden_in_stage_1"]


def test_protocol_references_build_plan_roadmap_phone_first_jeeves_and_repair() -> None:
    text = read(PROTOCOL)
    registry = load_registry()

    for phrase in [
        "`docs/SKELETON_BUILD_PLAN.md`",
        "`docs/DEVELOPMENT_DEPARTMENT_ROADMAP.md`",
        "Oleksii should receive short approve/reject decisions",
        "not raw logs as the main interface",
        "Future Jeeves autonomy is separate product and runtime work",
        "stop expanding that route",
        "minimal repair or rollback issue",
    ]:
        assert phrase in text

    assert registry["inputs"]["build_plan"] == "docs/SKELETON_BUILD_PLAN.md"
    assert registry["inputs"]["development_department_roadmap"] == (
        "docs/DEVELOPMENT_DEPARTMENT_ROADMAP.md"
    )
    assert registry["phone_first_operator_involvement"]["operator"] == "Oleksii"
    assert registry["phone_first_operator_involvement"]["main_interface"] == (
        "short_approve_reject_decisions"
    )
    assert registry["jeeves_boundary"]["skeleton_is_jeeves_runtime_adapter"] is False
    assert registry["repair_path"]["action"] == (
        "stop_expansion_and_create_minimal_repair_or_rollback_task"
    )
