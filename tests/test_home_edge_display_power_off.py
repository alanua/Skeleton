from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from core.home_edge import display_power_off
from core.home_edge.controller_auth import (
    DISPLAY_POWER_OFF_OPERATOR_APPROVAL,
    DISPLAY_POWER_OFF_PRIVACY_BOUNDARY,
    DISPLAY_POWER_OFF_TASK_ID,
    DISPLAY_POWER_OFF_TARGET_NODE,
    RUNTIME_MAINTENANCE_MODE,
)


def literal_body(*, risk: str | None = "yellow", extra: tuple[str, ...] = ()) -> str:
    lines = [
        f"Mode: {RUNTIME_MAINTENANCE_MODE}",
        f"Maintenance Task ID: {DISPLAY_POWER_OFF_TASK_ID}",
        f"Operator Approval: {DISPLAY_POWER_OFF_OPERATOR_APPROVAL}",
        f"Target Node: {DISPLAY_POWER_OFF_TARGET_NODE}",
        f"Privacy Boundary: {DISPLAY_POWER_OFF_PRIVACY_BOUNDARY}",
    ]
    if risk is not None:
        lines.append(f"Risk: {risk}")
    lines.extend(extra)
    lines.extend(("```task", "turn off the display", "```"))
    return "\n".join(lines)


@dataclass(frozen=True)
class Receipt:
    status: str = "ok"
    node_id: str = DISPLAY_POWER_OFF_TARGET_NODE
    exit_code: int = 0
    idempotency: str = "executed"
    receipt_hash: str = "receipt-hash"


def test_literal_2374_header_with_risk_yellow_reaches_signer_and_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_sign(request: dict[str, object]) -> dict[str, object]:
        captured["unsigned"] = dict(request)
        return {**request, "signature": "sha256=" + "a" * 64}

    def fake_execute(request: dict[str, object]) -> Receipt:
        captured["transport"] = dict(request)
        return Receipt()

    monkeypatch.setattr(display_power_off, "sign_display_power_off_request", fake_sign)
    monkeypatch.setattr(display_power_off, "execute_home_edge_request", fake_execute)

    receipt = display_power_off.execute_display_power_off_task(
        literal_body(),
        registered_clean_main_sha="a" * 40,
        github_main_sha="a" * 40,
    )

    assert receipt["physically_verified"] is True
    assert captured["unsigned"]["argv"] == ["xset", "dpms", "force", "off"]
    assert captured["unsigned"]["operator_approval_ref"] == (
        DISPLAY_POWER_OFF_OPERATOR_APPROVAL
    )
    assert captured["unsigned"]["public"] is True
    assert captured["transport"]["signature"] == "sha256=" + "a" * 64


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    (
        (f"Mode: {RUNTIME_MAINTENANCE_MODE}", "Mode: CODEX_TASK", "invalid_mode"),
        (
            f"Maintenance Task ID: {DISPLAY_POWER_OFF_TASK_ID}",
            "Maintenance Task ID: home_edge_01_lan_inventory_read_only",
            "unsupported_maintenance_task_id",
        ),
        (
            f"Operator Approval: {DISPLAY_POWER_OFF_OPERATOR_APPROVAL}",
            "Operator Approval: approved",
            "missing_operator_approval",
        ),
        (
            f"Target Node: {DISPLAY_POWER_OFF_TARGET_NODE}",
            "Target Node: home-edge-02",
            "unsupported_target_node",
        ),
        (
            f"Privacy Boundary: {DISPLAY_POWER_OFF_PRIVACY_BOUNDARY}",
            "Privacy Boundary: PUBLIC_SAFE_REPOSITORY_ONLY",
            "unsupported_privacy_boundary",
        ),
        ("Risk: yellow", "Risk: Yellow", "unsupported_risk"),
    ),
)
def test_authority_and_literal_risk_fields_fail_closed(
    monkeypatch: pytest.MonkeyPatch, old: str, new: str, reason: str
) -> None:
    monkeypatch.setattr(display_power_off, "sign_display_power_off_request", lambda _: {})
    body = literal_body().replace(old, new)

    with pytest.raises(ValueError, match=reason):
        display_power_off.execute_display_power_off_task(
            body,
            registered_clean_main_sha="a" * 40,
            github_main_sha="a" * 40,
        )


def test_missing_risk_fails_narrowly_before_signing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed = False

    def fake_sign(_request: dict[str, object]) -> dict[str, object]:
        nonlocal signed
        signed = True
        return {}

    monkeypatch.setattr(display_power_off, "sign_display_power_off_request", fake_sign)

    with pytest.raises(ValueError, match="unsupported_risk"):
        display_power_off.execute_display_power_off_task(
            literal_body(risk=None),
            registered_clean_main_sha="a" * 40,
            github_main_sha="a" * 40,
        )

    assert signed is False


def test_repository_and_expected_main_sha_body_fields_are_not_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        display_power_off,
        "sign_display_power_off_request",
        lambda request: {**request, "signature": "sha256=" + "b" * 64},
    )
    monkeypatch.setattr(display_power_off, "execute_home_edge_request", lambda _: Receipt())

    receipt = display_power_off.execute_display_power_off_task(
        literal_body(),
        registered_clean_main_sha="a" * 40,
        github_main_sha="a" * 40,
    )

    assert display_power_off.success_criteria_met(receipt)


def test_trusted_runtime_sha_must_match_before_signing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        display_power_off,
        "sign_display_power_off_request",
        lambda _request: pytest.fail("signer should not be called"),
    )

    with pytest.raises(ValueError, match="trusted_runtime_sha_mismatch"):
        display_power_off.execute_display_power_off_task(
            literal_body(),
            registered_clean_main_sha="a" * 40,
            github_main_sha="b" * 40,
        )


def test_receipt_status_lines_are_public_safe() -> None:
    lines = display_power_off.receipt_status_lines(
        {
            "status": "ok",
            "node_id": DISPLAY_POWER_OFF_TARGET_NODE,
            "exit_code": 0,
            "idempotency": "executed",
            "receipt_hash": "abc123",
            "physically_verified": True,
            "finished_at": datetime.now(UTC).isoformat(),
        }
    )

    assert "physically_verified=true" in lines
    assert not any("signature" in line or "hmac" in line.lower() for line in lines)
