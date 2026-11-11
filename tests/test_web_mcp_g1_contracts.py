from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest import mock

import jsonschema
import pytest

from core.operator_attention import (
    ATTENTION_SCHEMA,
    RESOLUTION_SCHEMA,
    AttentionResolutionStatus,
    OperatorAttentionResolution,
)
from core.web_session import SyntheticWebSessionAdapter, WEB_SESSION_SCHEMA, WebSessionBinding


ROOT = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict[str, object]:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def _resolution(attention: dict[str, object]) -> OperatorAttentionResolution:
    return OperatorAttentionResolution(
        attention_id=str(attention["attention_id"]),
        status=AttentionResolutionStatus.RESOLVED,
        actor_reference="operator:@oleksii",
        resolved_at="2026-10-03T00:01:00Z",
        resume_token=str(attention["resume_token"]),
        public_note="Operator solved the CAPTCHA in the exact private session.",
    )


def test_synthetic_web_session_captcha_handoff_and_exact_resume_contract() -> None:
    adapter = SyntheticWebSessionAdapter()
    opened = adapter.open_session(session_id="sess:g1-contract", url="https://example.test/login")

    attention = adapter.require_captcha(session_id=opened.binding.session_id, challenge_id="captcha:g1-login").to_dict()
    waiting = adapter.state(opened.binding.session_id)
    resumed = adapter.resume_after_attention(binding=opened.binding, resolution=_resolution(attention))

    assert attention["schema"] == ATTENTION_SCHEMA
    assert attention["kind"] == "captcha"
    assert attention["subject_ref"] == "web-session:sess:g1-contract"
    assert waiting.to_dict() == {
        "schema": WEB_SESSION_SCHEMA,
        "binding": {
            "provider_id": "synthetic-web",
            "session_id": "sess:g1-contract",
            "session_epoch": 1,
            "origin": "https://example.test",
        },
        "current_url": "https://example.test/login",
        "status": "waiting_for_operator",
        "pending_challenge": {
            "challenge_id": "captcha:g1-login",
            "page_url": "https://example.test/login",
            "reason_code": "CAPTCHA_OPERATOR_HANDOFF_REQUIRED",
            "resume_token": attention["resume_token"],
        },
    }
    assert resumed.to_dict()["status"] == "resumed"
    assert resumed.to_dict()["pending_challenge"] is None


def test_captcha_resume_rejects_different_session_binding() -> None:
    adapter = SyntheticWebSessionAdapter()
    opened = adapter.open_session(session_id="sess:captcha-a", url="https://example.test/login")
    adapter.open_session(session_id="sess:captcha-b", url="https://example.test/login")
    attention = adapter.require_captcha(session_id=opened.binding.session_id, challenge_id="captcha:login-a").to_dict()
    forged_binding = WebSessionBinding(
        provider_id=opened.binding.provider_id,
        session_id="sess:captcha-b",
        session_epoch=opened.binding.session_epoch,
        origin=opened.binding.origin,
    )

    with pytest.raises(ValueError, match="RESUME_REJECTED_SESSION_BINDING_MISMATCH"):
        adapter.resume_after_attention(binding=forged_binding, resolution=_resolution(attention))


def test_captcha_resume_rejects_replayed_token_for_other_challenge() -> None:
    adapter = SyntheticWebSessionAdapter()
    opened = adapter.open_session(session_id="sess:captcha-replay", url="https://example.test/login")
    attention = adapter.require_captcha(session_id=opened.binding.session_id, challenge_id="captcha:login-a").to_dict()
    forged = replace(_resolution(attention), resume_token="resume:" + ("0" * 64))

    with pytest.raises(ValueError, match="RESUME_REJECTED_TOKEN_MISMATCH"):
        adapter.resume_after_attention(binding=opened.binding, resolution=forged)


def test_operator_attention_resolution_is_typed_and_schema_valid() -> None:
    adapter = SyntheticWebSessionAdapter()
    opened = adapter.open_session(session_id="sess:schema", url="https://example.test/login")
    attention = adapter.require_captcha(session_id=opened.binding.session_id, challenge_id="captcha:schema").to_dict()
    resolution = _resolution(attention).to_dict()

    assert resolution["schema"] == RESOLUTION_SCHEMA
    jsonschema.validate(attention, _schema("operator_attention_request.schema.json"))
    jsonschema.validate(resolution, _schema("operator_attention_resolution.schema.json"))
    jsonschema.validate(adapter.state(opened.binding.session_id).to_dict(), _schema("web_session_state.schema.json"))


def test_fake_adapter_has_no_network_subprocess_or_live_browser_usage() -> None:
    adapter = SyntheticWebSessionAdapter()

    with mock.patch.object(subprocess, "run") as run, mock.patch.object(
        urllib.request, "urlopen"
    ) as urlopen:
        opened = adapter.open_session(session_id="sess:no-effects", url="https://example.test/login")
        attention = adapter.require_captcha(session_id=opened.binding.session_id, challenge_id="captcha:no-effects").to_dict()
        resumed = adapter.resume_after_attention(binding=opened.binding, resolution=_resolution(attention))

    assert resumed.to_dict()["status"] == "resumed"
    run.assert_not_called()
    urlopen.assert_not_called()


def _request(**changes):
    from core.operator_attention import OperatorAttentionRequest

    fields = dict(
        attention_id="attn:lifecycle", kind="captcha", priority="high",
        subject_ref="web-session:sess:private", created_at="2026-10-03T00:00:00Z",
        resume_token="resume:synthetic", public_summary="https://private.test/?secret=fixture",
        private_instruction="Synthetic private instruction", channel="telegram",
        action_class="human_challenge", idempotency_key="fixture:key",
        expires_at="2026-10-03T00:02:00Z",
    )
    fields.update(changes)
    return OperatorAttentionRequest(**fields)


def test_lifecycle_channels_transitions_idempotency_and_public_receipts():
    from core.operator_attention import OperatorAttentionLifecycle, AttentionStatus

    lifecycle = OperatorAttentionLifecycle()
    request = _request()
    receipt = lifecycle.submit(request)
    assert lifecycle.submit(request) == receipt
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        lifecycle.submit(replace(request, priority="low"))
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        lifecycle.submit(replace(request, attention_id="attn:another"))
    with pytest.raises(ValueError, match="INVALID_ATTENTION_TRANSITION"):
        lifecycle.advance(request.attention_id, AttentionStatus.RESOLVED, now=request.created_at)
    for status in (AttentionStatus.DELIVERED, AttentionStatus.ACKNOWLEDGED):
        lifecycle.advance(request.attention_id, status, now=request.created_at)
    resolution = _resolution(request.to_dict())
    resolved = lifecycle.resolve(resolution)
    assert lifecycle.resolve(resolution) == resolved
    assert resolved.to_dict() == {"attention_id": request.attention_id, "status": "resolved"}
    with pytest.raises(ValueError, match="RESOLUTION_CONFLICT"):
        lifecycle.resolve(replace(resolution, public_note="Different decision"))
    jsonschema.validate(request.to_dict(), _schema("operator_attention_request.schema.json"))


@pytest.mark.parametrize("status", ["rejected", "cancelled"])
def test_terminal_decisions_cannot_be_reversed(status):
    from core.operator_attention import OperatorAttentionLifecycle

    lifecycle = OperatorAttentionLifecycle()
    request = _request()
    lifecycle.submit(request)
    resolution = replace(_resolution(request.to_dict()), status=status)
    assert lifecycle.resolve(resolution).status.value == status
    jsonschema.validate(resolution.to_dict(), _schema("operator_attention_resolution.schema.json"))
    with pytest.raises(ValueError, match="RESOLUTION_CONFLICT"):
        lifecycle.resolve(_resolution(request.to_dict()))


def test_deadline_expires_and_keeps_exact_session_paused():
    adapter = SyntheticWebSessionAdapter()
    opened = adapter.open_session(session_id="sess:expiry", url="https://example.test/login")
    request = adapter.require_captcha(
        session_id=opened.binding.session_id, challenge_id="captcha:expiry",
        expires_at="2026-10-03T00:01:00Z",
    )
    with pytest.raises(ValueError, match="ATTENTION_ALREADY_TERMINAL"):
        adapter.resume_after_attention(binding=opened.binding, resolution=_resolution(request.to_dict()))
    assert adapter.state(opened.binding.session_id).status.value == "waiting_for_operator"
    assert adapter.attention.receipt(request.attention_id).status.value == "expired"
    with pytest.raises(ValueError, match="WEB_SESSION_ALREADY_EXISTS"):
        adapter.open_session(session_id=opened.binding.session_id, url="https://example.test/login")


def test_handoff_retry_cannot_replace_pending_challenge():
    adapter = SyntheticWebSessionAdapter()
    opened = adapter.open_session(session_id="sess:retry", url="https://example.test/login")
    args = dict(session_id=opened.binding.session_id, challenge_id="captcha:retry")
    request = adapter.require_captcha(**args)
    assert adapter.require_captcha(**args) == request
    with pytest.raises(ValueError, match="WEB_SESSION_ALREADY_WAITING"):
        adapter.require_captcha(session_id=opened.binding.session_id, challenge_id="captcha:other")
    for field, value in (("provider_id", "other-provider"), ("session_epoch", 2), ("origin", "https://other.test")):
        with pytest.raises(ValueError, match="SESSION_BINDING_MISMATCH"):
            adapter.resume_after_attention(binding=replace(opened.binding, **{field: value}), resolution=_resolution(request.to_dict()))
    adapter.resume_after_attention(binding=opened.binding, resolution=_resolution(request.to_dict()))
    with pytest.raises(ValueError, match="NO_PENDING_CHALLENGE"):
        adapter.resume_after_attention(binding=opened.binding, resolution=_resolution(request.to_dict()))


@pytest.mark.parametrize("changes", [
    {"created_at": "2026-02-30T00:00:00Z"},
    {"expires_at": "2026-10-02T00:00:00Z"},
    {"channel": "unsupported"}, {"action_class": "unsupported"},
    {"idempotency_key": "short"},
])
def test_attention_rejects_invalid_contract_fields(changes):
    with pytest.raises(ValueError):
        _request(**changes)


def test_state_invariants_and_url_credentials_are_rejected():
    adapter = SyntheticWebSessionAdapter()
    with pytest.raises(ValueError):
        adapter.open_session(session_id="sess:credentials", url="https://user:fixture@example.test/login")
    opened = adapter.open_session(session_id="sess:invariants", url="https://example.test/login")
    with pytest.raises(ValueError):
        replace(opened, status="waiting_for_operator")
    with pytest.raises(ValueError):
        replace(opened, current_url="https://other.test/login")
