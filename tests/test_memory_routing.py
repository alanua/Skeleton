from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


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


def test_secrets_route_uses_secret_manager_target() -> None:
    routes = load_yaml("MEMORY_ROUTING.yaml")["routes"]

    assert routes["secrets_credentials"]["target"] == "local_encrypted_store_or_secret_manager"


def test_role_boundaries_exist() -> None:
    roles = load_yaml("SOURCE_REGISTRY.yaml")["roles"]

    for role in ["chatgpt", "skeleton", "runner", "codex", "gemini", "jeeves"]:
        assert role in roles
        assert "means" in roles[role]
