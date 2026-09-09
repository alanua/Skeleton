from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_contracts import (
    EffectClass,
    OperationIR,
    PrivacyClass,
    Reversibility,
    classify_effects,
)

ROOT = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def test_green_reversible_effect_is_autonomous() -> None:
    decision = classify_effects(
        ["workspace_write"],
        reversibility=Reversibility.REVERSIBLE,
        privacy=PrivacyClass.PUBLIC_SAFE,
    )
    assert decision.effect_class is EffectClass.GREEN
    assert decision.separate_privileged_pep_required is False
    assert decision.reason_code == "GREEN_AUTONOMOUS_TYPED_EFFECT"


def test_private_data_does_not_self_escalate_effect_class() -> None:
    decision = classify_effects(
        ["private_compute"],
        reversibility=Reversibility.REVERSIBLE,
        privacy=PrivacyClass.PRIVATE,
    )
    assert decision.effect_class is EffectClass.GREEN


def test_yellow_effect_requires_separate_boundary() -> None:
    decision = classify_effects(
        ["protected_edit"],
        reversibility=Reversibility.BOUNDED_REVERSIBLE,
        privacy=PrivacyClass.PUBLIC_SAFE,
    )
    assert decision.effect_class is EffectClass.YELLOW
    assert decision.separate_privileged_pep_required is True


def test_red_effect_and_irreversible_work_require_privileged_pep() -> None:
    red = classify_effects(
        ["protected_merge"],
        reversibility=Reversibility.BOUNDED_REVERSIBLE,
        privacy=PrivacyClass.PUBLIC_SAFE,
    )
    irreversible = classify_effects(
        ["workspace_write"],
        reversibility=Reversibility.IRREVERSIBLE,
        privacy=PrivacyClass.PUBLIC_SAFE,
    )
    assert red.effect_class is EffectClass.RED
    assert irreversible.effect_class is EffectClass.RED
    assert red.separate_privileged_pep_required is True


def test_operation_ir_rejects_arbitrary_shell_kind() -> None:
    with pytest.raises(ValueError, match="ORDINARY_OPERATION_KIND_REQUIRED"):
        OperationIR("op-1", "root_shell", ("host",), ("read",), "idem-1")


def test_universal_task_schema_accepts_typed_task_and_rejects_self_authorization_field() -> None:
    schema = _schema("universal_runner_task.schema.json")
    task = {
        "schema": "skeleton.universal_runner_task.v1",
        "task_id": "task-1",
        "intent": "validate repository",
        "domain": "github",
        "target_resources": ["alanua/Skeleton"],
        "required_capabilities": ["repository_read", "test_execution"],
        "privacy": "PUBLIC_SAFE",
        "reversibility": "REVERSIBLE",
        "expected_effects": ["read", "test"],
        "validation": ["pytest"],
        "rollback": [],
        "idempotency_key": "task-1@state-a",
        "operator_boundary_evidence": [],
    }
    jsonschema.validate(task, schema)
    invalid = dict(task, approval_required=False)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, schema)


def test_operation_schema_has_no_shell_escape_kind() -> None:
    schema = _schema("runner_operation_ir.schema.json")
    allowed = schema["properties"]["kind"]["enum"]
    assert "shell" not in allowed
    assert "root_shell" not in allowed
    assert "arbitrary_command" not in allowed


def test_receipt_schema_requires_typed_reason_and_touched_resources() -> None:
    schema = _schema("runner_verification_receipt.schema.json")
    receipt = {
        "schema": "skeleton.runner_verification_receipt.v1",
        "operation_id": "op-1",
        "idempotency_key": "op-1@state-a",
        "reason_code": "VALIDATION_PASS",
        "validation_status": "PASS",
        "touched_resources": ["worktree:file.py"],
        "before_state_ref": "sha256:before",
        "after_state_ref": "sha256:after",
    }
    jsonschema.validate(receipt, schema)
