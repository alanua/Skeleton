from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "HERMES_PROVIDER_ROUTING.md"
SCHEMA_PATH = ROOT / "schemas" / "hermes_provider_routing.schema.json"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def policy_example(**overrides: object) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "schema": "hermes.provider_routing_policy.v0",
        "policy_id": "hermes-provider-routing-public-safe-v0",
        "public_safe": True,
        "live_provider_routes_enabled": False,
        "provider_names_are_examples": True,
        "roles": {
            "planner": {
                "role": "planner",
                "provider_example": "GPT/Codex class model",
                "provider_example_only": True,
                "live_route_enabled": False,
                "intended_use": [
                    "short planning",
                    "final decision framing",
                    "operator-facing summaries",
                ],
            },
            "bulk_worker": {
                "role": "bulk_worker",
                "provider_example": "DeepSeek through OpenRouter",
                "provider_example_only": True,
                "live_route_enabled": False,
                "intended_use": [
                    "high-volume public-safe extraction",
                    "sanitized chunk summarization",
                    "draft normalization",
                ],
            },
            "critic": {
                "role": "critic",
                "provider_example": "Gemini or comparable auditor",
                "provider_example_only": True,
                "live_route_enabled": False,
                "intended_use": [
                    "contradiction checks",
                    "scale calibration review",
                    "QA notes",
                ],
            },
        },
        "budget_gates": {
            "per_run_budget_required": True,
            "token_cap_required": True,
            "max_retries_required": True,
            "manual_approval_before_live_provider_use": True,
            "stop_on_unknown_cost_or_quota": True,
        },
        "privacy_gates": {
            "external_provider_private_data_default": "blocked",
            "approval_route_required_for_private_provider_use": True,
            "forbidden_without_approval": [
                "real_drawings",
                "drive_links_or_ids",
                "private_quantities",
                "customer_data",
                "secrets",
                "environment_values",
                "file_paths",
                "private_task_packets",
            ],
        },
        "aufmass_routing": {
            "private_local_hermes_handles_raw_artifacts": True,
            "cheap_worker_sanitized_chunks_only_if_approved": True,
            "critic_sanitized_aggregate_notes_only_if_approved": True,
            "raw_drive_sheets_drawings_private_only": True,
        },
        "failure_modes": [
            "model_drift",
            "hallucinated_dimensions",
            "private_leakage",
            "provider_outage",
            "quota_exhaustion",
            "inconsistent_calibration",
            "excessive_retries",
        ],
        "sanitized_payload_contract": {
            "sanitized_only": True,
            "no_live_provider_call": True,
            "forbidden_content_patterns": [
                r"(?i)https?://|drive\.google\.com|docs\.google\.com",
                r"\b[A-Za-z0-9_-]{25,}\b",
                r"(?i)(?:/[A-Za-z0-9._-]+){2,}|[A-Za-z]:\\|\\\\|(?:^|\s)~/",
                r"(?i)api[_-]?key|token|secret|password|sk-[A-Za-z0-9]",
                r"(?i)\b\d+(?:[.,]\d+)?\s?(?:m2|m²|sqm|lfm|m3|m³|stk|pcs|qty|quantity)\b",
            ],
        },
    }
    policy.update(overrides)
    return policy


def assert_schema_constants(schema: dict[str, Any], policy: dict[str, Any]) -> None:
    assert set(schema["required"]).issubset(policy)
    assert schema["properties"]["schema"]["const"] == policy["schema"]
    assert schema["properties"]["public_safe"]["const"] is True
    assert schema["properties"]["live_provider_routes_enabled"]["const"] is False
    assert schema["properties"]["provider_names_are_examples"]["const"] is True


def assert_no_forbidden_payload(policy: dict[str, Any], payload: object) -> None:
    text = json.dumps(payload, sort_keys=True)
    patterns = policy["sanitized_payload_contract"]["forbidden_content_patterns"]
    matches = [pattern for pattern in patterns if re.search(pattern, text)]
    assert matches == []


def test_policy_artifacts_exist() -> None:
    assert DOC_PATH.is_file()
    assert SCHEMA_PATH.is_file()


def test_policy_document_declares_static_public_safe_boundary() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")

    for expected in [
        "does not enable live provider calls",
        "All provider names in this document are policy examples",
        "They are not enabled live routes",
        "planner",
        "bulk_worker",
        "critic",
        "Troitsa-inspired pattern",
        "does not import, vendor, execute, or depend",
    ]:
        assert expected in text


def test_policy_document_defines_budget_privacy_and_aufmass_gates() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")

    for expected in [
        "per-run budget",
        "token cap",
        "maximum retry count",
        "Manual approval is required before any live provider use",
        "stop on unknown cost",
        "No external provider route may receive private material",
        "Real drawings",
        "Google Drive links",
        "Real quantities",
        "Customer names",
        "Secrets",
        "Local paths",
        "Private local/Hermes handling owns Google Drive, Sheets, raw drawings",
        "cheap worker may only receive sanitized chunks after approval",
        "critic may only receive sanitized aggregate notes",
    ]:
        assert expected in text


def test_policy_document_lists_required_failure_modes() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")

    for expected in [
        "Model drift",
        "Hallucinated dimensions",
        "Private leakage",
        "Provider outage",
        "Quota exhaustion",
        "Inconsistent calibration",
        "Excessive retries",
    ]:
        assert expected in text


def test_schema_requires_disabled_example_only_routes_and_gates() -> None:
    schema = load_schema()
    policy = policy_example()

    assert_schema_constants(schema, policy)
    assert schema["additionalProperties"] is False

    role_schema = schema["$defs"]["route_role"]["properties"]
    assert role_schema["provider_example_only"]["const"] is True
    assert role_schema["live_route_enabled"]["const"] is False

    budget = schema["properties"]["budget_gates"]["properties"]
    assert budget["manual_approval_before_live_provider_use"]["const"] is True
    assert budget["stop_on_unknown_cost_or_quota"]["const"] is True

    privacy = schema["properties"]["privacy_gates"]["properties"]
    assert privacy["external_provider_private_data_default"]["const"] == "blocked"
    forbidden = set(privacy["forbidden_without_approval"]["items"]["enum"])
    assert {
        "real_drawings",
        "drive_links_or_ids",
        "private_quantities",
        "customer_data",
        "secrets",
        "environment_values",
        "file_paths",
        "private_task_packets",
    }.issubset(forbidden)


def test_policy_example_accepts_sanitized_public_safe_payload() -> None:
    policy = policy_example()
    payload = {
        "chunk_id": "sanitized-chunk-001",
        "source": "synthetic public-safe example",
        "summary": "Compare room label consistency using anonymized calibration notes.",
        "quantity_note": "No exact private quantities included.",
    }

    assert_no_forbidden_payload(policy, payload)


def test_policy_examples_reject_live_secret_material() -> None:
    policy = policy_example()
    payload = {"provider": "example", "api_key": "sk-live-secret-token"}

    try:
        assert_no_forbidden_payload(policy, payload)
    except AssertionError:
        return
    raise AssertionError("secret-like payload was accepted")


def test_policy_examples_reject_raw_urls() -> None:
    policy = policy_example()
    payload = {"source": "https://drive.google.com/file/d/example/view"}

    try:
        assert_no_forbidden_payload(policy, payload)
    except AssertionError:
        return
    raise AssertionError("raw URL payload was accepted")


def test_policy_examples_reject_file_paths() -> None:
    policy = policy_example()
    payload = {"source": "/home/operator/private/aufmass/customer-a.pdf"}

    try:
        assert_no_forbidden_payload(policy, payload)
    except AssertionError:
        return
    raise AssertionError("file path payload was accepted")


def test_policy_examples_reject_drive_ids() -> None:
    policy = policy_example()
    payload = {"drive_file_id": "1A2b3C4d5E6f7G8h9I0jK1lM2nO3pQ"}

    try:
        assert_no_forbidden_payload(policy, payload)
    except AssertionError:
        return
    raise AssertionError("Drive ID payload was accepted")


def test_policy_examples_reject_private_quantities() -> None:
    policy = policy_example()
    payload = {"room": "private-room-a", "wall_area": "42.7 m2"}

    try:
        assert_no_forbidden_payload(policy, payload)
    except AssertionError:
        return
    raise AssertionError("private quantity payload was accepted")
