from __future__ import annotations

import pytest

from core.runner_vnext_adapters import ExecutionEnvelope
from core.runner_vnext_contracts import EffectClass, PrivacyClass
from core.runner_vnext_ledger import OperationIdentity, OperationLedger
from core.runner_vnext_pep import AuthorityClaim, PEPError, PolicyEnforcementPoint, PrivilegedBrokerRequest, reservation_scope_fingerprint


class Verifier:
    def __init__(self, result: bool = True) -> None: self.result = result
    def verify(self, claim: AuthorityClaim) -> bool: return self.result


def envelope(effect=EffectClass.GREEN, *, idem="idem:1", target="git:abc", fence=7, adapter="adapter:repo", resources=("repo:core/a.py",), effects=("workspace_write",)) -> ExecutionEnvelope:
    reason = "GREEN_AUTONOMOUS_TYPED_EFFECT" if effect is EffectClass.GREEN else ("RED_EFFECT_BOUNDARY" if effect is EffectClass.RED else "YELLOW_EFFECT_BOUNDARY")
    return ExecutionEnvelope("op:1", adapter, target, fence, idem, resources, effects, effect, reason, ("repository_write_allowlisted",), PrivacyClass.PUBLIC_SAFE)


def ledger_for(env: ExecutionEnvelope, *, reserved_target=None, reserved_fence=None, scope_env=None) -> OperationLedger:
    ledger = OperationLedger()
    identity = OperationIdentity(env.operation_id, env.idempotency_key, reserved_target or env.target_state_ref)
    ledger.reserve(identity, fence_token=reserved_fence or env.fence_token, reservation_scope_hash=reservation_scope_fingerprint(scope_env or env))
    return ledger


def claim(effect=EffectClass.YELLOW, fresh=True, **overrides) -> AuthorityClaim:
    values = dict(evidence_refs=("github:approval-1",), operation_id="op:1", target_state_ref="git:abc", effect_class=effect, resources=("repo:core/a.py",), fresh=fresh)
    values.update(overrides); return AuthorityClaim(**values)


def test_green_authorizes_only_exact_reserved_envelope() -> None:
    env = envelope(); receipt = PolicyEnforcementPoint().evaluate(env, ledger_for(env))
    assert receipt.authorization_status == "AUTHORIZED_GREEN_BOUNDED" and receipt.side_effects_executed is False


def test_stale_ledger_fence_target_and_missing_reservation_fail_closed() -> None:
    env = envelope()
    with pytest.raises(PEPError, match="PEP_LEDGER_FENCE_MISMATCH"):
        PolicyEnforcementPoint().evaluate(env, ledger_for(env, reserved_fence=8))
    with pytest.raises(PEPError, match="PEP_LEDGER_TARGET_STATE_MISMATCH"):
        PolicyEnforcementPoint().evaluate(env, ledger_for(env, reserved_target="git:def"))
    missing = envelope(idem="idem:missing")
    with pytest.raises(PEPError, match="PEP_LEDGER_RESERVATION_MISSING"):
        PolicyEnforcementPoint().evaluate(missing, OperationLedger())


def test_yellow_requires_verified_exact_authority_and_only_builds_broker_request() -> None:
    env = envelope(EffectClass.YELLOW); ledger = ledger_for(env)
    with pytest.raises(PEPError, match="PEP_OPERATOR_AUTHORITY_REQUIRED"): PolicyEnforcementPoint().evaluate(env, ledger)
    request = PolicyEnforcementPoint().evaluate(env, ledger, authority_claim=claim(), authority_verifier=Verifier())
    assert isinstance(request, PrivilegedBrokerRequest) and request.execution_authorized is False


def test_forged_or_mismatched_authority_cannot_self_authorize() -> None:
    env = envelope(EffectClass.YELLOW); ledger = ledger_for(env)
    with pytest.raises(PEPError, match="PEP_AUTHORITY_VERIFICATION_FAILED"): PolicyEnforcementPoint().evaluate(env, ledger, authority_claim=claim(), authority_verifier=Verifier(False))
    with pytest.raises(PEPError, match="PEP_AUTHORITY_TARGET_STATE_MISMATCH"): PolicyEnforcementPoint().evaluate(env, ledger, authority_claim=claim(target_state_ref="git:def"), authority_verifier=Verifier())
    with pytest.raises(PEPError, match="PEP_AUTHORITY_SCOPE_MISMATCH"): PolicyEnforcementPoint().evaluate(env, ledger, authority_claim=claim(resources=("repo:other.py",)), authority_verifier=Verifier())


def test_red_requires_fresh_explicit_verified_authority() -> None:
    env = envelope(EffectClass.RED); ledger = ledger_for(env)
    with pytest.raises(PEPError, match="PEP_FRESH_OPERATOR_AUTHORITY_REQUIRED"): PolicyEnforcementPoint().evaluate(env, ledger, authority_claim=claim(EffectClass.RED, fresh=False), authority_verifier=Verifier())
    request = PolicyEnforcementPoint().evaluate(env, ledger, authority_claim=claim(EffectClass.RED), authority_verifier=Verifier())
    assert request.effect_class is EffectClass.RED and request.execution_authorized is False


def test_green_cannot_smuggle_authority_claim() -> None:
    env=envelope()
    with pytest.raises(PEPError, match="PEP_GREEN_AUTHORITY_CLAIM_UNEXPECTED"): PolicyEnforcementPoint().evaluate(env, ledger_for(env), authority_claim=claim(EffectClass.GREEN), authority_verifier=Verifier())


def test_ledger_binds_full_envelope_scope_not_just_ids() -> None:
    base = envelope(); widened = envelope(resources=("repo:core/a.py", "repo:core/b.py"))
    with pytest.raises(PEPError, match="PEP_LEDGER_ENVELOPE_FINGERPRINT_MISMATCH"):
        PolicyEnforcementPoint().evaluate(widened, ledger_for(base))


def test_ledger_binds_effect_and_adapter_identity() -> None:
    base = envelope(); forged = envelope(adapter="adapter:other", effects=("test",))
    with pytest.raises(PEPError, match="PEP_LEDGER_ENVELOPE_FINGERPRINT_MISMATCH"):
        PolicyEnforcementPoint().evaluate(forged, ledger_for(base))


def test_fence_rebind_preserves_scope_and_uses_current_authoritative_fence() -> None:
    original = envelope(fence=7)
    ledger = ledger_for(original)
    ident = OperationIdentity(original.operation_id, original.idempotency_key, original.target_state_ref)
    ledger.rebind_fence(ident, expected_fence_token=7, new_fence_token=8)
    rebound = envelope(fence=8)
    receipt = PolicyEnforcementPoint().evaluate(rebound, ledger)
    assert receipt.fence_token == 8
    with pytest.raises(PEPError, match="PEP_LEDGER_FENCE_MISMATCH"):
        PolicyEnforcementPoint().evaluate(original, ledger)


def test_terminal_operation_cannot_be_reauthorized() -> None:
    env = envelope()
    ledger = ledger_for(env)
    ident = OperationIdentity(env.operation_id, env.idempotency_key, env.target_state_ref)
    ledger.finish(ident, fence_token=env.fence_token, success=True, reason_code="VALIDATION_PASS",
                  touched_resource_refs=("repo:core/a.py",), before_state_ref="sha256:a",
                  after_state_ref="sha256:b", validation_status="PASS")
    with pytest.raises(PEPError, match="PEP_LEDGER_RESERVATION_REQUIRED"):
        PolicyEnforcementPoint().evaluate(env, ledger)
