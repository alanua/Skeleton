from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from core.runner_vnext_compat import LegacyTaskObservation
from core.runner_vnext_contracts import EffectClass
from core.runner_vnext_shadow import DivergenceKind, ShadowParityHarness

ROOT = Path(__file__).resolve().parents[1]


def obs(*, legacy=EffectClass.GREEN, path="core/example.py", task_kind="code_edit", legacy_status="runner:ready") -> LegacyTaskObservation:
    return LegacyTaskObservation(
        source_task_ref="github:issue-2",
        target_state_ref="git:def",
        repo="alanua/Skeleton",
        branch="runner/test",
        task_kind=task_kind,
        requested_capabilities=("repository_read","repository_write_allowlisted"),
        allowed_files=(path,),
        privacy_boundary="PUBLIC_SAFE_REPOSITORY_ONLY",
        legacy_effect_class=legacy,
        legacy_status=legacy_status,
        evidence_refs=("github:comment-2",),
    )


def test_shadow_match_and_zero_side_effect_contract() -> None:
    receipt = ShadowParityHarness().evaluate(obs())
    assert receipt.divergence is DivergenceKind.MATCH
    assert receipt.vnext_effect_class is EffectClass.GREEN
    assert receipt.side_effects_executed is False


def test_protected_path_surfaces_vnext_stricter_divergence() -> None:
    receipt = ShadowParityHarness().evaluate(obs(path="scripts/runner_poll_github_tasks.py"))
    assert receipt.vnext_effect_class is EffectClass.YELLOW
    assert receipt.divergence is DivergenceKind.VNEXT_STRICTER
    assert receipt.vnext_reason_code == "YELLOW_EFFECT_BOUNDARY"


def test_legacy_stricter_and_incomparable_are_visible_not_auto_resolved() -> None:
    stricter = ShadowParityHarness().evaluate(obs(legacy=EffectClass.YELLOW))
    assert stricter.divergence is DivergenceKind.LEGACY_STRICTER
    incomparable = ShadowParityHarness().evaluate(obs(legacy=None))
    assert incomparable.divergence is DivergenceKind.INCOMPARABLE


def test_invalid_legacy_input_is_a_receipt_not_an_execution_attempt() -> None:
    receipt = ShadowParityHarness().evaluate(obs(task_kind="unknown"))
    assert receipt.divergence is DivergenceKind.INVALID_LEGACY_INPUT
    assert receipt.vnext_effect_class is None
    assert receipt.vnext_reason_code == "LEGACY_TASK_KIND_UNSUPPORTED"
    assert receipt.side_effects_executed is False


def test_shadow_receipt_schema_is_closed_and_side_effects_are_const_false() -> None:
    schema = json.loads((ROOT / "schemas" / "runner_shadow_parity_receipt.schema.json").read_text())
    payload = {
        "schema":"skeleton.runner_shadow_parity_receipt.v1",
        "source_task_ref":"github:issue-2",
        "target_state_ref":"git:def",
        "legacy_effect_class":"GREEN",
        "legacy_status":"runner:ready",
        "vnext_effect_class":"YELLOW",
        "vnext_reason_code":"YELLOW_EFFECT_BOUNDARY",
        "divergence":"VNEXT_STRICTER",
        "evidence_refs":["github:comment-2"],
        "side_effects_executed":False,
    }
    jsonschema.validate(payload, schema)
    payload["side_effects_executed"] = True
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("schema must reject side effects")



def test_invalid_private_refs_are_redacted_from_failure_receipt() -> None:
    bad = LegacyTaskObservation(
        source_task_ref="/private/task", target_state_ref="/private/state",
        repo="alanua/Skeleton", branch="runner/test", task_kind="code_edit",
        requested_capabilities=("repository_read",), allowed_files=("core/example.py",),
        privacy_boundary="PUBLIC_SAFE_REPOSITORY_ONLY", legacy_effect_class=EffectClass.GREEN,
        legacy_status="runner:ready", evidence_refs=("/private/evidence", "github:comment-3"),
    )
    receipt = ShadowParityHarness().evaluate(bad)
    assert receipt.divergence is DivergenceKind.INVALID_LEGACY_INPUT
    assert receipt.source_task_ref == "redacted:invalid-source-ref"
    assert receipt.target_state_ref == "redacted:invalid-target-ref"
    assert receipt.evidence_refs == ("github:comment-3",)


def test_invalid_legacy_status_is_redacted_from_failure_receipt() -> None:
    receipt = ShadowParityHarness().evaluate(obs(legacy_status="/private/status"))
    assert receipt.divergence is DivergenceKind.INVALID_LEGACY_INPUT
    assert receipt.legacy_status == "redacted:invalid-legacy-status"
    assert "/private/status" not in receipt.legacy_status
