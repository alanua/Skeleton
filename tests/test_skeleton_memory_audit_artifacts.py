from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs/audits/SKELETON_SOURCE_INVENTORY.md"
LEDGER = ROOT / "docs/audits/SKELETON_DECISION_CONFLICT_LEDGER.yaml"
MIGRATION_MAP = ROOT / "docs/audits/SKELETON_MEMORY_MIGRATION_MAP.yaml"

DECISION_STATES = {
    "CURRENT",
    "SUPERSEDED",
    "ABSORBED",
    "CONFLICT",
    "HISTORICAL",
    "REJECTED",
    "NEEDS_OPERATOR",
}
IMPLEMENTATION_STATES = {
    "LIVE",
    "PARTIAL",
    "CONTRACT_ONLY",
    "PLANNED",
    "BLOCKED",
    "NOT_PLANNED",
}
MIGRATION_ACTIONS = {
    "IMPORT_PRIVATE",
    "REWRITE_PUBLIC_CANON",
    "KEEP_AS_EVIDENCE",
    "SUPERSEDE",
    "IGNORE_ARCHIVED",
    "NEEDS_OPERATOR",
}
FORBIDDEN_PUBLIC_MARKERS = (
    "/" + "home/",
    "C" + ":\\",
    "BEGIN " + "PRIVATE KEY",
    "password" + "=",
    "token" + "=",
    "api" + "_key",
    "secret" + "=",
    "raw" + "_chat",
)


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_required_audit_artifacts_exist_and_parse() -> None:
    assert INVENTORY.exists()
    assert load_yaml(LEDGER)["schema"] == "skeleton.audit.decision_conflict_ledger.v1"
    assert load_yaml(MIGRATION_MAP)["schema"] == "skeleton.audit.memory_migration_map.v1"


def test_public_artifacts_do_not_expose_private_values_or_local_paths() -> None:
    for path in (INVENTORY, LEDGER, MIGRATION_MAP):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for marker in FORBIDDEN_PUBLIC_MARKERS:
            assert marker.lower() not in lowered, (path, marker)


def test_decision_ledger_entries_use_required_states_and_source_refs() -> None:
    ledger = load_yaml(LEDGER)
    source_ids = {source["id"] for source in ledger["sources"]}

    assert set(ledger["allowed_decision_states"]) == DECISION_STATES
    assert set(ledger["allowed_implementation_states"]) == IMPLEMENTATION_STATES
    assert ledger["current_main_sha"] == "0f49c719e1106720141d90c2e058c057d54db326"

    for decision in ledger["decisions"]:
        assert decision["decision_state"] in DECISION_STATES
        assert decision["implementation_state"] in IMPLEMENTATION_STATES
        assert decision["source_refs"], decision["id"]
        assert set(decision["source_refs"]) <= source_ids
        assert "authority" in decision
        assert "freshness" in decision
        assert decision["public_safe_summary"]


def test_decision_summary_counts_match_entries() -> None:
    ledger = load_yaml(LEDGER)
    decisions = ledger["decisions"]

    decision_counts = {state: 0 for state in DECISION_STATES}
    implementation_counts = {state: 0 for state in IMPLEMENTATION_STATES}
    for decision in decisions:
        decision_counts[decision["decision_state"]] += 1
        implementation_counts[decision["implementation_state"]] += 1

    assert ledger["summary_counts"]["decision_states"] == decision_counts
    assert ledger["summary_counts"]["implementation_states"] == implementation_counts


def test_migration_map_uses_allowed_actions_and_aggregate_only_status() -> None:
    migration = load_yaml(MIGRATION_MAP)
    assert set(migration["allowed_actions"]) == MIGRATION_ACTIONS
    assert migration["memory_gateway_contract"]["direct_sqlite_writes"] == "forbidden"
    assert migration["memory_gateway_contract"]["value_payloads_in_git"] == "forbidden"
    assert migration["aggregate_counts"]["import_results"]["imported"] == 0

    action_counts = {action: 0 for action in MIGRATION_ACTIONS}
    for source in migration["source_categories"]:
        assert source["migration_action"] in MIGRATION_ACTIONS
        assert source["source_refs"], source["id"]
        assert source["target_domain"]
        assert source["privacy_class"]
        assert source["dedupe_strategy"]
        assert source["conflict_strategy"]
        assert source["validation_plan"]
        action_counts[source["migration_action"]] += 1

    assert migration["aggregate_counts"]["source_categories"] == len(migration["source_categories"])
    assert migration["aggregate_counts"]["action_counts"] == action_counts


def test_inventory_mentions_required_public_safe_counts_and_blockers() -> None:
    text = INVENTORY.read_text(encoding="utf-8")

    assert "| Visible repository files in this checkout | 622 |" in text
    assert "| Root control/registry YAML files | 12 |" in text
    assert "| Known private/local source categories | 12 |" in text
    assert "projects/skeleton/STATE.yaml" in text
    assert "MemoryGateway" in text
    assert "This worktree phase may publish only public-safe audit artifacts and tests." in text
