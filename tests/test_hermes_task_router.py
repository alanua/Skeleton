from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.hermes_task_router import (
    ROUTE_ALIASES,
    RouteUnavailableError,
    UnsafeRouteDowngradeError,
    route_hermes_task,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "hermes_task_packet.schema.json"
DOC_PATH = ROOT / "docs" / "HERMES_PROVIDER_ROUTING.md"


def synthetic_task(**overrides: object) -> dict[str, object]:
    task: dict[str, object] = {
        "task_id": "SYNTH-ROUTE-001",
        "title": "Normalize synthetic room labels",
        "goal": "Route a synthetic public-safe subtask.",
        "task_type": "normalization",
        "ambiguity": "low",
        "impact": "low",
        "evidence_quality": "sufficient",
        "privacy_class": "synthetic",
        "retry_count": 0,
        "max_retries": 2,
        "operator_gate": False,
        "scope": ["synthetic Aufmass route classification"],
        "allowed_files": ["tests/test_hermes_task_router.py"],
        "expected_outputs": ["draft_pr"],
        "budget": {"budget_units": 1, "token_cap": 1200},
        "evidence": ["synthetic_fixture_metadata"],
    }
    task.update(overrides)
    return task


def test_low_tasks_route_to_public_safe_worker_alias() -> None:
    packet = route_hermes_task(synthetic_task(task_type="deterministic_extraction"))

    assert packet["route"]["classification"] == "LOW"
    assert packet["route"]["alias"] == "AUFMASS_WORKER_LOW"
    assert packet["route"]["transition"] == "STAY"
    assert packet["route"]["budget"] == {
        "budget_units": 1,
        "token_cap": 1200,
        "live_route_enabled": False,
    }
    assert packet["route"]["privacy"] == {
        "class": "synthetic",
        "public_safe": True,
        "no_secrets": True,
    }


def test_mid_tasks_route_to_review_alias_and_low_escalates_to_mid() -> None:
    packet = route_hermes_task(
        synthetic_task(
            task_type="candidate_generation",
            ambiguity="medium",
            impact="medium",
            previous_route="LOW",
        )
    )

    assert packet["route"]["classification"] == "MID"
    assert packet["route"]["alias"] == "AUFMASS_REVIEW_MID"
    assert packet["route"]["transition"] == "LOW->MID"
    assert "ambiguity:mid" in packet["route"]["reason_codes"]


def test_mid_rework_transition_keeps_review_alias_explicit() -> None:
    packet = route_hermes_task(
        synthetic_task(
            task_type="rework_instruction",
            previous_route="MID",
            rework_ready=True,
        )
    )

    assert packet["route"]["classification"] == "MID"
    assert packet["route"]["alias"] == "AUFMASS_REVIEW_MID"
    assert packet["route"]["transition"] == "MID->LOW_REWORK"


def test_high_tasks_route_to_expert_alias() -> None:
    packet = route_hermes_task(
        synthetic_task(
            task_type="method_approval",
            ambiguity="high",
            impact="high",
            operator_gate=True,
        )
    )

    assert packet["route"]["classification"] == "HIGH"
    assert packet["route"]["alias"] == "AUFMASS_EXPERT_HIGH"
    assert "operator_gate:required" in packet["route"]["reason_codes"]


def test_mid_to_high_transition_for_unresolved_high_impact_ambiguity() -> None:
    packet = route_hermes_task(
        synthetic_task(
            task_type="geometry_review",
            ambiguity="unresolved",
            impact="high",
            previous_route="MID",
        )
    )

    assert packet["route"]["classification"] == "HIGH"
    assert packet["route"]["transition"] == "MID->HIGH"


def test_high_requests_evidence_when_evidence_is_insufficient() -> None:
    packet = route_hermes_task(
        synthetic_task(
            task_type="final_expert_adjudication",
            impact="high",
            evidence_quality="insufficient",
            evidence=["synthetic_missing_calibration_note"],
        )
    )

    assert packet["route"]["classification"] == "HIGH"
    assert packet["route"]["alias"] == "AUFMASS_EXPERT_HIGH"
    assert packet["route"]["transition"] == "HIGH->REQUEST_EVIDENCE"
    assert packet["route"]["evidence"]["contains_real_artifacts"] is False


def test_never_silently_downgrades_high_requested_as_low() -> None:
    with pytest.raises(UnsafeRouteDowngradeError):
        route_hermes_task(
            synthetic_task(task_type="method_rejection"),
            requested_alias="AUFMASS_WORKER_LOW",
        )


def test_fail_closed_when_requested_alias_is_unavailable() -> None:
    with pytest.raises(RouteUnavailableError):
        route_hermes_task(
            synthetic_task(task_type="normalization"),
            requested_alias="AUFMASS_WORKER_LOW",
            available_aliases={"AUFMASS_REVIEW_MID", "AUFMASS_EXPERT_HIGH"},
        )


def test_task_packet_schema_declares_public_alias_route_metadata() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    route_schema = schema["$defs"]["task_route"]

    assert set(route_schema["required"]) == {
        "classification",
        "alias",
        "transition",
        "reason_codes",
        "budget",
        "retry",
        "evidence",
        "privacy",
        "operator_approval",
    }
    assert route_schema["properties"]["alias"]["enum"] == list(ROUTE_ALIASES.values())
    schema_text = json.dumps(route_schema).lower()
    for forbidden_name in ["openai", "anthropic", "gemini", "deepseek", "gpt"]:
        assert forbidden_name not in schema_text


def test_provider_routing_doc_describes_alias_router_without_live_providers() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")

    for expected in [
        "AUFMASS_WORKER_LOW",
        "AUFMASS_REVIEW_MID",
        "AUFMASS_EXPERT_HIGH",
        "LOW->MID",
        "MID->LOW_REWORK",
        "MID->HIGH",
        "HIGH->REQUEST_EVIDENCE",
        "Never silently downgrade HIGH",
        "Fail closed",
        "provider/model names stay out of public task packets",
    ]:
        assert expected in text
