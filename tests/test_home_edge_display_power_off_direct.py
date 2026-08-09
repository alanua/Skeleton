from __future__ import annotations

import json

import pytest

from core.home_edge import display_power_off as display
from core.home_edge.executor import HomeEdgeExecRequest, sign_request
from core.home_edge.executor_gateway import EXEC_HMAC_SECRET_ENV


SECRET = "test-home-edge-secret"


def signer_stdin(**updates: object) -> str:
    payload: dict[str, object] = dict(display.SIGNER_STDIN)
    payload.update(updates)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def test_fixed_signer_accepts_only_exact_metadata_and_signs_fixed_request() -> None:
    envelope = display.signer_envelope_from_stdin(
        signer_stdin(),
        argv=[],
    )
    with pytest.raises(ValueError, match="signer_argv_rejected"):
        display.signer_envelope_from_stdin(signer_stdin(), argv=["--anything"])

    parsed = HomeEdgeExecRequest.from_mapping(envelope)
    assert parsed.signature == sign_request(parsed, SECRET)
    assert parsed.node_id == display.TARGET_NODE
    assert parsed.operator_approval_ref == display.OPERATOR_APPROVAL
    assert parsed.idempotency_key == display.IDEMPOTENCY_KEY
    assert parsed.argv == ()
    assert parsed.script == display.DISPLAY_POWER_OFF_SCRIPT
    assert "xset dpms force off" in parsed.script


@pytest.fixture(autouse=True)
def hmac_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXEC_HMAC_SECRET_ENV, SECRET)


@pytest.mark.parametrize(
    "updates",
    [
        {"privacy_boundary": "PRIVATE_RUNTIME_STATE_PUBLIC_SAFE_STATUZ"},
        {"operator_approval_ref": display.OPERATOR_APPROVAL + "_EXTRA"},
        {"maintenance_task_id": display.TASK_ID + "_extra"},
        {"target_node": "home-edge-02"},
        {"extra": "field"},
    ],
)
def test_fixed_signer_rejects_near_miss_metadata(updates: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="signer_stdin"):
        display.signer_envelope_from_stdin(signer_stdin(**updates), argv=[])


def test_signed_authority_validation_rejects_mutable_request_changes() -> None:
    request = display.build_signed_display_off_request().to_mapping()
    display.validate_signed_display_off_request(request)
    for field, value in (
        ("operator_approval_ref", "wrong"),
        ("idempotency_key", "changed"),
        ("script", "xset dpms force off"),
        ("argv", ["/usr/bin/xset", "dpms", "force", "off"]),
    ):
        changed = dict(request)
        changed[field] = value
        with pytest.raises(ValueError, match="display_power_off_signed_request_authority_mismatch"):
            display.validate_signed_display_off_request(changed)
    changed = dict(request)
    changed["node_id"] = "home-edge-02"
    with pytest.raises(ValueError, match="display_power_off_signed_request_authority_mismatch"):
        display.validate_signed_display_off_request(changed)
