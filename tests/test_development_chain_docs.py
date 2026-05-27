from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "PHONE_FIRST_DEVELOPMENT_CHAIN.md"
REGISTRY_PATH = ROOT / "DEVELOPMENT_CHAIN.yaml"


def load_registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def test_phone_first_development_chain_doc_exists() -> None:
    assert DOC_PATH.is_file()


def test_development_chain_registry_exists_and_is_registry_only() -> None:
    registry = load_registry()

    assert registry["schema"] == "skeleton.development_chain.v1"
    assert registry["status"] == "STAGE_0_DOCUMENTED_REGISTRY_ONLY"
    assert registry["stage_0_boundaries"]["runtime_enforcement"] is False
    assert registry["stage_0_boundaries"]["telegram_callback_changes"] is False
    assert registry["stage_0_boundaries"]["antigravity_automation"] is False


def test_doc_names_canonical_surfaces() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "GitHub Issues remain the task queue" in doc
    assert "GitHub PRs remain the review and merge surface" in doc
    assert "Telegram is only the phone approval console" in doc


def test_registry_has_required_safe_gates() -> None:
    gates = load_registry()["safe_gates"]

    assert gates["no_merge_without_approval"] is True
    assert gates["no_deploy_without_approval"] is True
    assert gates["no_secrets_runtime_or_production_database_access_without_approval"] is True
    assert gates["no_autonomous_roadmap_changes"] is True
    assert gates["no_direct_main_edits"] is True
    assert gates["manual_pr_fallback_validate_exact_head_sha_before_merge"] is True


def test_doc_spells_out_exact_head_sha_manual_fallback() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "Manual PR fallback must validate the exact PR head SHA before merge." in doc


def test_phone_first_output_order_is_machine_readable() -> None:
    phone_output = load_registry()["phone_first_output"]

    assert phone_output["order"] == [
        "plain_human_summary_first_paragraph",
        "result_status",
        "risk",
        "exactly_one_recommended_next_action",
        "details_link_or_expandable_logs",
    ]
    assert phone_output["exactly_one_recommended_next_action"] is True
    assert phone_output["logs_inline_by_default"] is False


def test_doc_includes_phone_first_reporting_requirements() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "First paragraph: plain human summary." in doc
    assert "Result/status." in doc
    assert "Risk." in doc
    assert "Exactly one recommended next action." in doc
    assert "Details link or expandable section for logs." in doc


def test_worker_selection_is_documented_and_machine_readable() -> None:
    registry = load_registry()
    workers = registry["workers"]
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "bounded_allowed_file_code_tasks" in workers["codex"]["select_for"]
    assert "multi_file_analysis" in workers["openhands"]["select_for"]
    assert workers["antigravity"]["allowed_only_through"] == "controlled_task_contract"
    assert workers["antigravity"]["implemented_in_stage_0"] is False
    assert "Codex is selected for bounded code tasks" in doc
    assert "OpenHands is selected for debugging" in doc
    assert "No Antigravity automation is implemented here." in doc


def test_gemini_and_claude_review_uses_are_registered() -> None:
    reviewers = load_registry()["reviewers"]

    assert "high_risk_review" in reviewers["gemini"]["useful_for"]
    assert "security_review" in reviewers["gemini"]["useful_for"]
    assert "architectural_critique" in reviewers["claude"]["useful_for"]
