from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_contracts import EffectClass
from core.runner_vnext_recovery import (
    COMMAND_EFFECT_CEILING,
    RecoveryCommand,
    RecoveryContext,
    RecoveryError,
    RecoveryHandlerResult,
    RecoveryKernel,
    RecoveryRequest,
)

ROOT = Path(__file__).resolve().parents[1]


def _handler(request: RecoveryRequest, _context: RecoveryContext) -> RecoveryHandlerResult:
    return RecoveryHandlerResult(
        reason_code="RECOVERY_VERIFIED",
        touched_resource_refs=(request.bounded_resources[0],),
        before_state_ref="sha256:before",
        after_state_ref="sha256:after",
        validation_status="PASS",
    )


def _request(command: RecoveryCommand = RecoveryCommand.CHECKOUT_FRESHNESS_CANARY) -> RecoveryRequest:
    return RecoveryRequest(
        request_id="r1",
        command=command,
        target_state_ref="git:abc",
        idempotency_key="r1@git:abc",
        bounded_resources=("runner:checkout",),
        expected_effects=("read",),
    )


def test_command_set_is_closed_and_never_red() -> None:
    assert {item.value for item in RecoveryCommand} == {
        "refresh_trust_anchor_bundle",
        "poller_reload_and_stale_reactivation",
        "checkout_freshness_canary",
        "typed_repair_runtime_state",
        "queue_fence_reset",
    }
    assert EffectClass.RED not in set(COMMAND_EFFECT_CEILING.values())


def test_kernel_executes_only_registered_typed_handler() -> None:
    request = _request()
    kernel = RecoveryKernel({request.command: _handler})
    receipt = kernel.execute(request, RecoveryContext(current_target_state_ref="git:abc"))
    assert receipt.status == "DONE"
    assert receipt.reason_code == "RECOVERY_VERIFIED"
    assert receipt.effect_ceiling is EffectClass.GREEN


def test_target_state_mismatch_fails_closed() -> None:
    request = _request()
    kernel = RecoveryKernel({request.command: _handler})
    with pytest.raises(RecoveryError, match="RECOVERY_TARGET_STATE_MISMATCH"):
        kernel.execute(request, RecoveryContext(current_target_state_ref="git:def"))


def test_queue_fence_reset_requires_current_matching_fence() -> None:
    request = RecoveryRequest(
        request_id="r2",
        command=RecoveryCommand.QUEUE_FENCE_RESET,
        target_state_ref="git:abc",
        idempotency_key="r2@git:abc@f7",
        bounded_resources=("runner:queue-fence",),
        expected_effects=("control_plane_change",),
        current_fence_token=7,
    )
    kernel = RecoveryKernel({request.command: _handler})
    with pytest.raises(RecoveryError, match="RECOVERY_FENCE_EVIDENCE_REQUIRED"):
        kernel.execute(request, RecoveryContext(current_target_state_ref="git:abc"))
    with pytest.raises(RecoveryError, match="RECOVERY_STALE_FENCE_EVIDENCE"):
        kernel.execute(request, RecoveryContext(current_target_state_ref="git:abc", current_fence_token=8))
    receipt = kernel.execute(request, RecoveryContext(current_target_state_ref="git:abc", current_fence_token=7))
    assert receipt.effect_ceiling is EffectClass.YELLOW


def test_public_receipt_rejects_private_path_like_refs() -> None:
    def private_handler(_request: RecoveryRequest, _context: RecoveryContext) -> RecoveryHandlerResult:
        return RecoveryHandlerResult("RECOVERY_VERIFIED", ("/private/path",), "sha256:a", "sha256:b", "PASS")
    request = _request()
    kernel = RecoveryKernel({request.command: private_handler})
    with pytest.raises(RecoveryError, match="RECOVERY_PUBLIC_REF_REQUIRED"):
        kernel.execute(request, RecoveryContext(current_target_state_ref="git:abc"))


def test_arbitrary_command_is_rejected_by_schema() -> None:
    schema = json.loads((ROOT / "schemas" / "runner_recovery_request.schema.json").read_text())
    payload = {
        "schema": "skeleton.runner_recovery_request.v1",
        "request_id": "r1",
        "command": "shell",
        "target_state_ref": "git:abc",
        "idempotency_key": "r1@git:abc",
        "bounded_resources": ["runner:checkout"],
        "expected_effects": ["read"],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)


def test_receipt_schema_excludes_red_effect_ceiling() -> None:
    schema = json.loads((ROOT / "schemas" / "runner_recovery_receipt.schema.json").read_text())
    assert schema["properties"]["effect_ceiling"]["enum"] == ["GREEN", "YELLOW"]


def test_command_effect_ceiling_is_enforced_from_expected_effects() -> None:
    green_request = RecoveryRequest(
        request_id="r3",
        command=RecoveryCommand.CHECKOUT_FRESHNESS_CANARY,
        target_state_ref="git:abc",
        idempotency_key="r3@git:abc",
        bounded_resources=("runner:checkout",),
        expected_effects=("control_plane_change",),
    )
    kernel = RecoveryKernel({green_request.command: _handler})
    with pytest.raises(RecoveryError, match="RECOVERY_EFFECT_CEILING_EXCEEDED"):
        kernel.execute(green_request, RecoveryContext(current_target_state_ref="git:abc"))

    red_request = RecoveryRequest(
        request_id="r4",
        command=RecoveryCommand.TYPED_REPAIR_RUNTIME_STATE,
        target_state_ref="git:abc",
        idempotency_key="r4@git:abc",
        bounded_resources=("runner:runtime",),
        expected_effects=("deploy",),
    )
    kernel = RecoveryKernel({red_request.command: _handler})
    with pytest.raises(RecoveryError, match="RECOVERY_RED_EFFECT_PROHIBITED"):
        kernel.execute(red_request, RecoveryContext(current_target_state_ref="git:abc"))


def test_handler_cannot_escape_bounded_resources_or_leak_state_refs() -> None:
    request = _request()
    def widened(_request: RecoveryRequest, _context: RecoveryContext) -> RecoveryHandlerResult:
        return RecoveryHandlerResult("RECOVERY_VERIFIED", ("runner:other",), "sha256:a", "sha256:b", "PASS")
    with pytest.raises(RecoveryError, match="RECOVERY_RESOURCE_SCOPE_EXCEEDED"):
        RecoveryKernel({request.command: widened}).execute(request, RecoveryContext(current_target_state_ref="git:abc"))

    def leaked(_request: RecoveryRequest, _context: RecoveryContext) -> RecoveryHandlerResult:
        return RecoveryHandlerResult("RECOVERY_VERIFIED", ("runner:checkout",), "/private/before", "sha256:b", "PASS")
    with pytest.raises(RecoveryError, match="RECOVERY_PUBLIC_REF_REQUIRED"):
        RecoveryKernel({request.command: leaked}).execute(request, RecoveryContext(current_target_state_ref="git:abc"))


def test_handler_reason_code_is_machine_safe() -> None:
    request = _request()
    def bad_reason(_request: RecoveryRequest, _context: RecoveryContext) -> RecoveryHandlerResult:
        return RecoveryHandlerResult("bad reason", ("runner:checkout",), "sha256:a", "sha256:b", "PASS")
    with pytest.raises(RecoveryError, match="RECOVERY_REASON_CODE_INVALID"):
        RecoveryKernel({request.command: bad_reason}).execute(request, RecoveryContext(current_target_state_ref="git:abc"))
