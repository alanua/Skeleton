from __future__ import annotations

from core.gate_engine import GateEngine, GateResult
from core.patch_validator import PatchValidator


def valid_plan() -> dict:
    return {
        "schema": "skeleton.patch_plan.v1",
        "target_files": ["README.md"],
        "change_type": "docs",
        "reason": "Clarify a documented rule.",
        "current_rule_read": True,
        "critique": "Small documentation-only patch.",
        "minimal_patch": "Add one sentence.",
        "verification": ["python3 -m pytest -q", "git diff --check"],
        "approval_required": True,
        "operator_approval": True,
    }


def test_gate_blocks_without_plan() -> None:
    decision = GateEngine().check_patch_plan(None)

    assert decision.result is GateResult.BLOCKED_NO_PLAN


def test_gate_blocked_decision_has_non_empty_reasons() -> None:
    decision = GateEngine().check_patch_plan(None)

    assert decision.reasons


def test_gate_blocks_invalid_schema() -> None:
    plan = valid_plan()
    plan["schema"] = "wrong.schema"

    decision = GateEngine().check_patch_plan(plan)

    assert decision.result is GateResult.BLOCKED_INVALID_SCHEMA


def test_gate_blocks_without_read_confirmation() -> None:
    plan = valid_plan()
    plan["current_rule_read"] = False

    decision = GateEngine().check_patch_plan(plan)

    assert decision.result is GateResult.BLOCKED_READ_MISSING


def test_gate_blocks_without_operator_approval() -> None:
    plan = valid_plan()
    plan["operator_approval"] = False

    decision = GateEngine().check_patch_plan(plan)

    assert decision.result is GateResult.BLOCKED_NO_APPROVAL


def test_gate_allows_valid_approved_plan() -> None:
    decision = GateEngine().check_patch_plan(valid_plan())

    assert decision.result is GateResult.ALLOWED


def test_gate_allows_valid_plan_when_approval_not_required() -> None:
    plan = valid_plan()
    plan["approval_required"] = False
    plan["operator_approval"] = False

    decision = GateEngine().check_patch_plan(plan)

    assert decision.result is GateResult.ALLOWED


def test_validator_returns_readable_errors_for_missing_fields() -> None:
    plan = valid_plan()
    del plan["reason"]
    del plan["verification"]

    errors = PatchValidator().validate(plan)

    assert "missing required field: reason" in errors
    assert "missing required field: verification" in errors
