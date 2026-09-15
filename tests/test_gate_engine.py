from __future__ import annotations

from core.gate_engine import GateEngine, GateResult


def patch_plan(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "skeleton.patch_plan.v1",
        "target_files": ["core/action_gate.py"],
        "change_type": "protected_control_plane",
        "reason": "change protected gate policy",
        "current_rule_read": True,
        "critique": "protected change requires explicit review",
        "minimal_patch": "edit one policy file",
        "verification": ["python3 -m pytest tests/test_gate_engine.py -q"],
        "approval_required": True,
        "operator_approval": True,
    }
    value.update(overrides)
    return value


def test_patch_plan_gate_allows_approved_protected_change() -> None:
    decision = GateEngine().check_patch_plan(patch_plan())

    assert decision.result is GateResult.ALLOWED


def test_patch_plan_gate_blocks_when_operator_approval_required_but_missing() -> None:
    decision = GateEngine().check_patch_plan(
        patch_plan(operator_approval=False),
    )

    assert decision.result is GateResult.BLOCKED_NO_APPROVAL


def test_patch_plan_gate_allows_policy_that_does_not_require_operator_approval() -> None:
    decision = GateEngine().check_patch_plan(
        patch_plan(approval_required=False, operator_approval=False),
    )

    assert decision.result is GateResult.ALLOWED
