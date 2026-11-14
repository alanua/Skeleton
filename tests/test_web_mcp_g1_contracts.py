from __future__ import annotations

import json
import socket
import subprocess
import urllib.request
import webbrowser
from dataclasses import replace
from pathlib import Path
from unittest import mock

import jsonschema
import pytest

from core.operator_attention import (
    AttentionReason, AttentionStatus, AttentionChannel, WebActionClass,
    AttentionResolutionStatus, OperatorAttentionResolution, _display,
)
from core.web_session import SyntheticWebSessionAdapter, WebSessionStatus

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-03T00:00:00Z"
LATER = "2026-10-03T00:01:00Z"


def schema(name):
    return json.loads((ROOT / 'schemas' / (name + '.schema.json')).read_text())


def setup(reason=AttentionReason.CAPTCHA, **kwargs):
    adapter = SyntheticWebSessionAdapter()
    opened = adapter.open_session(session_id="sess:fixture", url="https://example.test/private?fixture=secret")
    request = adapter.require_challenge(session_id=opened.binding.session_id,
                                        challenge_id="challenge:fixture", reason=reason, **kwargs)
    return adapter, opened, request


def resolution(request, **changes):
    fields = dict(attention_id=request.attention_id, status=AttentionResolutionStatus.COMPLETED,
                  actor_reference="operator:synthetic", resolved_at=LATER, resume_token=request.resume_token)
    fields.update(changes)
    return OperatorAttentionResolution(**fields)


def progress(adapter, request):
    for status in (AttentionStatus.DELIVERED, AttentionStatus.OPENED, AttentionStatus.IN_PROGRESS):
        assert adapter.attention.advance(request.attention_id, status, now=NOW)['status'] == status.value


def test_exact_enums():
    assert {r.name for r in AttentionReason} == {
        'CAPTCHA', 'MFA', 'OAUTH_CONSENT', 'ACCOUNT_SELECTION', 'SITE_CONSENT',
        'MANUAL_REVIEW', 'UNKNOWN_INTERACTIVE_CHALLENGE'}
    assert {s.name for s in AttentionStatus} == {
        'PENDING', 'DELIVERED', 'OPENED', 'IN_PROGRESS', 'COMPLETED', 'EXPIRED', 'CANCELLED'}
    assert {c.name for c in WebActionClass} == {
        'READ_ONLY', 'PRIVATE_SESSION_MUTATION', 'EXTERNAL_MUTATION', 'HIGH_IMPACT'}


@pytest.mark.parametrize('reason', list(AttentionReason))
def test_typed_challenges_schema_and_exact_session_resume(reason):
    adapter, opened, request = setup(reason)
    assert request.reason is reason
    assert adapter.state(opened.binding.session_id).status is WebSessionStatus.WAITING_FOR_OPERATOR
    assert request.browser_session_ref.startswith('session:')
    assert request.source_capability == 'web'
    assert request.source_operation == 'interactive_challenge'
    assert request.status is AttentionStatus.PENDING
    progress(adapter, request)
    result = resolution(request)
    jsonschema.validate(request.to_private_dict(), schema('operator_attention_request'),
                        format_checker=jsonschema.FormatChecker())
    jsonschema.validate(result.to_private_dict(), schema('operator_attention_resolution'),
                        format_checker=jsonschema.FormatChecker())
    jsonschema.validate(adapter.state(opened.binding.session_id).to_private_dict(), schema('web_session_state'))
    resumed = adapter.resume_after_attention(binding=opened.binding, resolution=result)
    assert resumed.status is WebSessionStatus.RESUMED
    assert resumed.binding == opened.binding
    assert resumed.current_url == opened.current_url
    assert resumed.pending_challenge is None
    assert adapter.attention.receipt(request.attention_id)['status'] == 'completed'
    assert adapter.attention.resolve(result)['status'] == 'completed'
    with pytest.raises(ValueError, match='NO_PENDING_CHALLENGE'):
        adapter.resume_after_attention(binding=opened.binding, resolution=result)
    jsonschema.validate(resumed.to_private_dict(), schema('web_session_state'))


def test_canonical_dual_channel_projection_and_idempotency():
    adapter, opened, request = setup()
    duplicate = adapter.require_challenge(session_id=opened.binding.session_id,
                                          challenge_id='challenge:fixture', reason='captcha')
    assert duplicate is request
    assert adapter.attention.submit(replace(request, attention_id='attn:alternate')) is request
    projections = adapter.attention.projections(request.attention_id)
    assert set(projections) == {'android_home', 'telegram'}
    assert {p['attention_id'] for p in projections.values()} == {request.request_id}
    progress(adapter, request)
    adapter.resume_after_attention(binding=opened.binding, resolution=resolution(request))
    assert all(p['status'] == 'completed' for p in adapter.attention.projections(request.attention_id).values())
    with pytest.raises(ValueError, match='IDEMPOTENCY_CONFLICT'):
        adapter.attention.submit(replace(request, source_operation='different'))


def test_explicit_bounded_channel_subset():
    adapter, _, request = setup(delivery_channels=(AttentionChannel.TELEGRAM,))
    assert set(adapter.attention.projections(request.attention_id)) == {'telegram'}
    for channels in ((), ('telegram', 'telegram'), ('local',), tuple(AttentionChannel) + ('telegram',)):
        with pytest.raises(ValueError):
            replace(request, delivery_channels=channels)


@pytest.mark.parametrize('target', ['opened', 'completed', 'expired', 'resolved', 'acknowledged', 'rejected'])
def test_invalid_transition_fails_closed(target):
    adapter, opened, request = setup()
    with pytest.raises(ValueError):
        adapter.attention.advance(request.attention_id, target, now=NOW)
    with pytest.raises(ValueError):
        adapter.resume_after_attention(binding=opened.binding, resolution=resolution(request))
    assert adapter.attention.receipt(request.attention_id)['status'] == 'pending'
    assert adapter.state(opened.binding.session_id).status is WebSessionStatus.WAITING_FOR_OPERATOR


@pytest.mark.parametrize('terminal', ['expired', 'cancelled'])
def test_terminal_state_all_projections_non_actionable_and_never_resume(terminal):
    adapter, opened, request = setup(expires_at=LATER)
    if terminal == 'expired':
        adapter.attention.expire(request.attention_id, now=LATER)
    else:
        adapter.attention.advance(request.attention_id, 'cancelled', now=NOW)
    with pytest.raises(ValueError):
        adapter.resume_after_attention(binding=opened.binding, resolution=resolution(request))
    for status in AttentionStatus:
        with pytest.raises(ValueError):
            adapter.attention.advance(request.attention_id, status, now=LATER)
    assert all(p['status'] == terminal for p in adapter.attention.projections(request.attention_id).values())
    assert adapter.state(opened.binding.session_id).status is WebSessionStatus.WAITING_FOR_OPERATOR


def test_completion_at_deadline_cannot_resume():
    adapter, opened, request = setup(expires_at=LATER)
    progress(adapter, request)
    with pytest.raises(ValueError):
        adapter.resume_after_attention(binding=opened.binding, resolution=resolution(request))
    assert adapter.attention.receipt(request.attention_id)['status'] == 'expired'


@pytest.mark.parametrize('field,value', [('provider_id', 'other-provider'), ('session_epoch', 2),
                                         ('origin', 'https://other.test'), ('session_id', 'sess:other')])
def test_wrong_session_fails_closed(field, value):
    adapter, opened, request = setup()
    adapter.open_session(session_id='sess:other', url='https://example.test/login')
    progress(adapter, request)
    with pytest.raises(ValueError, match='SESSION_BINDING_MISMATCH'):
        adapter.resume_after_attention(binding=replace(opened.binding, **{field: value}), resolution=resolution(request))
    assert adapter.attention.receipt(request.attention_id)['status'] == 'in_progress'


@pytest.mark.parametrize('changes', [{'resume_token': 'resume:wrong-token'}, {'attention_id': 'attn:wrong-request'}])
def test_wrong_request_or_token_fails_closed(changes):
    adapter, opened, request = setup()
    progress(adapter, request)
    with pytest.raises(ValueError):
        adapter.resume_after_attention(binding=opened.binding, resolution=resolution(request, **changes))
    assert adapter.state(opened.binding.session_id).status is WebSessionStatus.WAITING_FOR_OPERATOR


def test_arbitrary_prose_never_creates_attention():
    adapter = SyntheticWebSessionAdapter()
    opened = adapter.open_session(session_id='sess:prose', url='https://example.test/CAPTCHA?text=NEEDS_OPERATOR')
    assert opened.status is WebSessionStatus.OPEN
    assert adapter.attention._requests == {}
    with pytest.raises(ValueError):
        adapter.require_challenge(session_id=opened.binding.session_id, challenge_id='challenge:prose',
                                  reason='CAPTCHA NEEDS_OPERATOR')
    assert adapter.attention._requests == {}


@pytest.mark.parametrize('url', ['http://example.test/', 'https://user:fixture@example.test/login',
                                'https://user@example.test/', 'https://@example.test/'])
def test_url_rejections(url):
    with pytest.raises(ValueError):
        SyntheticWebSessionAdapter().open_session(session_id='sess:invalid', url=url)


@pytest.mark.parametrize('changes', [
    {'created_at': '2026-02-30T00:00:00Z'}, {'expires_at': '2026-13-01T00:00:00Z'},
    {'expires_at': NOW}, {'created_at': '2026-10-03T00:00:00+00:00'},
    {'idempotency_key': 'short'}, {'source_capability': 'https://private.test'},
    {'browser_session_ref': 'https://private.test'},
    {'display_title': 'password=fixture'}, {'display_message': 'https://private.test/secret'},
])
def test_bounded_request_validation(changes):
    _, _, request = setup()
    with pytest.raises(ValueError):
        replace(request, **changes)


def test_public_receipt_allowlist_and_private_values():
    adapter, opened, request = setup()
    receipt = adapter.attention.receipt(request.attention_id)
    allowed = {'attention_id', 'reason', 'status', 'source_capability', 'source_operation',
               'created_at', 'expires_at', 'delivery_channels', 'display_title', 'display_message', 'reason_code'}
    assert set(receipt) == allowed
    serialized = json.dumps(receipt)
    for key in ('url', 'current_url', 'page_url', 'origin', 'resume_token', 'cookie', 'cookies',
                'credential', 'password', 'token', 'private_instruction', 'page', 'body', 'text',
                'screenshot', 'image', 'browser_profile_path', 'raw_challenge_details', 'browser_session_ref'):
        assert key not in receipt
    for private in (opened.current_url, opened.binding.origin, request.resume_token,
                    request.browser_session_ref, 'fixture=secret', 'private.test',
                    'Synthetic private instruction', 'cookie-fixture', '/private/browser/profile'):
        assert private not in serialized
    assert request.resume_token.split(':', 1)[1] not in serialized
    assert request.public_receipt() == receipt
    assert not hasattr(request, 'to_dict')
    assert not hasattr(resolution(request), 'to_dict')


def test_synthetic_adapter_performs_no_io():
    def forbidden(*args, **kwargs):
        raise AssertionError('external call attempted')
    with mock.patch.object(subprocess, 'run', forbidden), mock.patch.object(subprocess, 'Popen', forbidden), \
         mock.patch.object(urllib.request, 'urlopen', forbidden), mock.patch.object(socket, 'socket', forbidden), \
         mock.patch.object(webbrowser, 'open', forbidden):
        adapter, opened, request = setup()
        progress(adapter, request)
        adapter.resume_after_attention(binding=opened.binding, resolution=resolution(request))


def test_schema_enum_parity_and_rejects_extra_public_fields():
    request_schema = schema('operator_attention_request')
    for name, enum in [('reason', AttentionReason), ('status', AttentionStatus)]:
        assert request_schema['properties'][name]['enum'] == [x.value for x in enum]
    assert request_schema['properties']['delivery_channels']['items']['enum'] == [x.value for x in AttentionChannel]
    assert schema('operator_attention_resolution')['properties']['status']['enum'] == [x.value for x in AttentionResolutionStatus]
    _, _, request = setup()
    for changes in ({'status': 'resolved'}, {'reason': 'review'}, {'delivery_channels': ['local']},
                    {'private_instruction': 'fixture'}, {'display_message': 'raw page text'}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(dict(request.to_private_dict(), **changes), request_schema)
