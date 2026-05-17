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


def test_role_boundaries_exist() -> None:
    roles = load_yaml("SOURCE_REGISTRY.yaml")["roles"]

    for role in ["chatgpt", "skeleton", "runner", "codex", "gemini", "jeeves"]:
        assert role in roles
        assert "means" in roles[role]
