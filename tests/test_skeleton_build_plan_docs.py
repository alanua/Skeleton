from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BUILD_PLAN = ROOT / "docs" / "SKELETON_BUILD_PLAN.md"
ROADMAP = ROOT / "docs" / "DEVELOPMENT_DEPARTMENT_ROADMAP.md"
REGISTRY = ROOT / "docs" / "SKELETON_BUILD_PLAN.yaml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_plan_documents_exist() -> None:
    assert BUILD_PLAN.is_file()
    assert ROADMAP.is_file()
    assert REGISTRY.is_file()


def test_build_plan_records_skeleton_jeeves_boundary_and_source_of_truth() -> None:
    text = read(BUILD_PLAN)

    assert "Skeleton is the human-controlled construction and control layer" in text
    assert "Skeleton is not the product being built" in text
    assert "Jeeves is a separate future assistant product and runtime" in text
    assert "GitHub `main` in `alanua/Skeleton` is the source of truth" in text
    assert "The boot entrypoint is `BOOT_MANIFEST.yaml`" in text
    assert "`projects/skeleton/STATE.yaml` is handoff state only, not canon truth" in text


def test_build_plan_records_operator_rules_as_registry_not_runtime_gate() -> None:
    text = read(BUILD_PLAN)

    assert "`OPERATOR_RULES.yaml` is a stage 1 registry" in text
    assert "not a runtime gate" in text
    assert "not an autonomous enforcement layer" in text


def test_build_plan_forbids_runtime_autonomy_and_names_next_milestones() -> None:
    text = read(BUILD_PLAN)

    for concept in [
        "No runtime integration",
        "No Telegram callback code change",
        "No Antigravity automation",
        "No publisher implementation",
        "No dashboard implementation",
        "No deploy",
        "No service restart",
        "No secrets or environment handling",
        "No merge automation",
        "No autonomous agent behavior",
        "Audit open stale PRs",
        "Rebuild a clean publisher/delivery route from current `main`",
        "exact head SHA validation",
    ]:
        assert concept in text


def test_development_department_roadmap_records_roles_and_control_chain() -> None:
    text = read(ROADMAP)

    for role in [
        "Oleksii",
        "ChatGPT",
        "Runner",
        "Codex",
        "OpenHands",
        "Antigravity",
        "Gemini",
        "Claude",
        "Telegram",
        "GitHub Issues",
        "GitHub PRs",
    ]:
        assert role in text

    for chain_step in [
        "Intake starts",
        "GitHub Issues carry the task scope",
        "Runner picks an approved issue",
        "Tests and `git diff --check` validate the result",
        "A GitHub PR carries the diff",
        "Oleksii gives small but real approval",
        "reviewed head SHA is known",
    ]:
        assert chain_step in text


def test_development_department_roadmap_records_phone_and_antigravity_boundaries() -> None:
    text = read(ROADMAP)

    assert "Phone-first does not mean the whole system is Telegram" in text
    assert "Phone-first operation remains important" in text
    assert "it is one constraint inside the larger managed department plan" in text
    assert "Antigravity should be introduced later as a controlled development cockpit" in text
    assert "Antigravity must not become canon" in text
    assert "must not merge, deploy, handle secrets, change runtime services" in text


def test_development_department_roadmap_records_backlog_publisher_and_phases() -> None:
    text = read(ROADMAP)

    assert "Old PRs must be audited against current `main`" in text
    assert "The stale phone-first-only PR #584 stays closed" in text
    assert "The publisher/delivery route must be rebuilt cleanly from current `main`" in text

    for phase in [
        "Phase 0: Source Of Truth And Boot Route Stabilization",
        "Phase 1: Rules And Operating Standard Visibility",
        "Phase 2: Reliable Task Queue And Delivery Route",
        "Phase 3: PR Backlog Cleanup And Review Discipline",
        "Phase 4: Managed Roles And Worker Selection",
        "Phase 5: Antigravity, OpenHands, Gemini, And Claude Adapters",
        "Phase 6: Dashboard And State Reporting",
        "Phase 7: Future Jeeves Bridge",
    ]:
        assert phase in text


def test_machine_readable_registry_matches_public_safe_plan_boundaries() -> None:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))

    assert registry["schema"] == "skeleton.build_plan_registry.v1"
    assert registry["source_of_truth"]["repo"] == "alanua/Skeleton"
    assert registry["source_of_truth"]["ref"] == "main"
    assert registry["source_of_truth"]["boot_entrypoint"] == "BOOT_MANIFEST.yaml"
    assert registry["boundaries"]["jeeves_role"] == "separate_future_assistant_product_and_runtime"
    assert registry["boundaries"]["operator_rules_stage"] == "registry_only_not_runtime_gate"
    assert registry["boundaries"]["phone_first"] == "constraint_not_whole_plan"
    assert registry["boundaries"]["antigravity"] == "controlled_dev_cockpit_or_worker_candidate_not_source_of_truth"
    assert "autonomous_merge" in registry["forbidden_in_this_plan"]
    assert "runtime_changes" in registry["forbidden_in_this_plan"]
    assert "audit_open_stale_prs" in registry["next_milestones"]
    assert "rebuild_clean_publisher_delivery_route_from_current_main" in registry["next_milestones"]
