from __future__ import annotations

import pytest

from core.runner_vnext_adapters import ExecutionEnvelope
from core.runner_vnext_contracts import EffectClass, PrivacyClass
from core.runner_vnext_pep import AuthorityClaim, LedgerReservationEvidence, PEPError, PolicyEnforcementPoint, PrivilegedBrokerRequest, envelope_fingerprint


class Verifier:
    def __init__(self, result: bool = True) -> None: self.result = result
    def verify(self, claim: AuthorityClaim) -> bool: return self.result


def envelope(effect=EffectClass.GREEN) -> ExecutionEnvelope:
    return ExecutionEnvelope("op:1", "adapter:repo", "git:abc", 7, "idem:1", ("repo:core/a.py",), ("workspace_write",), effect, "GREEN_AUTONOMOUS_TYPED_EFFECT" if effect is EffectClass.GREEN else "YELLOW_EFFECT_BOUNDARY", ("repository_write_allowlisted",), PrivacyClass.PUBLIC_SAFE)


def ledger(env: ExecutionEnvelope | None = None, **overrides) -> LedgerReservationEvidence:
    base_env = env or envelope()
    values = dict(operation_id="op:1", idempotency_key="idem:1", target_state_ref="git:abc", fence_token=7, status="RESERVED", envelope_fingerprint=envelope_fingerprint(base_env))
    values.update(overrides); return LedgerReservationEvidence(**values)


def claim(effect=EffectClass.YELLOW, fresh=True, **overrides) -> AuthorityClaim:
    values = dict(evidence_refs=("github:approval-1",), operation_id="op:1", target_state_ref="git:abc", effect_class=effect, resources=("repo:core/a.py",), fresh=fresh)
    values.update(overrides); return AuthorityClaim(**values)


def test_green_authorizes_only_exact_reserved_envelope() -> None:
    receipt = PolicyEnforcementPoint().evaluate(envelope(), ledger())
    assert receipt.authorization_status == "AUTHORIZED_GREEN_BOUNDED"
    assert receipt.side_effects_executed is False


def test_stale_ledger_fence_target_and_idempotency_fail_closed() -> None:
    for bad, reason in [(ledger(fence_token=8), "PEP_LEDGER_FENCE_MISMATCH"), (ledger(target_state_ref="git:def"), "PEP_LEDGER_TARGET_STATE_MISMATCH"), (ledger(idempotency_key="idem:2"), "PEP_LEDGER_IDEMPOTENCY_MISMATCH")]:
        with pytest.raises(PEPError, match=reason): PolicyEnforcementPoint().evaluate(envelope(), bad)


def test_yellow_requires_verified_exact_authority_and_only_builds_broker_request() -> None:
    env = envelope(EffectClass.YELLOW)
    with pytest.raises(PEPError, match="PEP_OPERATOR_AUTHORITY_REQUIRED"): PolicyEnforcementPoint().evaluate(env, ledger(env))
    request = PolicyEnforcementPoint().evaluate(env, ledger(env), authority_claim=claim(), authority_verifier=Verifier())
    assert isinstance(request, PrivilegedBrokerRequest)
    assert request.execution_authorized is False


def test_forged_or_mismatched_authority_cannot_self_authorize() -> None:
    env = envelope(EffectClass.YELLOW)
    with pytest.raises(PEPError, match="PEP_AUTHORITY_VERIFICATION_FAILED"): PolicyEnforcementPoint().evaluate(env, ledger(env), authority_claim=claim(), authority_verifier=Verifier(False))
    with pytest.raises(PEPError, match="PEP_AUTHORITY_TARGET_STATE_MISMATCH"): PolicyEnforcementPoint().evaluate(env, ledger(env), authority_claim=claim(target_state_ref="git:def"), authority_verifier=Verifier())
    with pytest.raises(PEPError, match="PEP_AUTHORITY_SCOPE_MISMATCH"): PolicyEnforcementPoint().evaluate(env, ledger(env), authority_claim=claim(resources=("repo:other.py",)), authority_verifier=Verifier())


def test_red_requires_fresh_explicit_verified_authority() -> None:
    env = envelope(EffectClass.RED)
    with pytest.raises(PEPError, match="PEP_FRESH_OPERATOR_AUTHORITY_REQUIRED"): PolicyEnforcementPoint().evaluate(env, ledger(env), authority_claim=claim(EffectClass.RED, fresh=False), authority_verifier=Verifier())
    request = PolicyEnforcementPoint().evaluate(env, ledger(env), authority_claim=claim(EffectClass.RED), authority_verifier=Verifier())
    assert request.effect_class is EffectClass.RED and request.execution_authorized is False


def test_green_cannot_smuggle_authority_claim() -> None:
    with pytest.raises(PEPError, match="PEP_GREEN_AUTHORITY_CLAIM_UNEXPECTED"): PolicyEnforcementPoint().evaluate(envelope(), ledger(), authority_claim=claim(EffectClass.GREEN), authority_verifier=Verifier())


def test_ledger_binds_full_envelope_scope_not_just_ids() -> None:
    base = envelope()
    evidence = ledger()
    widened = ExecutionEnvelope(base.operation_id, base.adapter_id, base.target_state_ref, base.fence_token, base.idempotency_key, ("repo:core/a.py", "repo:core/b.py"), base.effects, base.effect_class, base.policy_reason_code, base.required_capabilities, base.privacy)
    with pytest.raises(PEPError, match="PEP_LEDGER_ENVELOPE_FINGERPRINT_MISMATCH"):
        PolicyEnforcementPoint().evaluate(widened, evidence)


def test_ledger_binds_effect_and_adapter_identity() -> None:
    base = envelope()
    evidence = ledger()
    forged = ExecutionEnvelope(base.operation_id, "adapter:other", base.target_state_ref, base.fence_token, base.idempotency_key, base.resources, ("test",), base.effect_class, base.policy_reason_code, base.required_capabilities, base.privacy)
    with pytest.raises(PEPError, match="PEP_LEDGER_ENVELOPE_FINGERPRINT_MISMATCH"):
        PolicyEnforcementPoint().evaluate(forged, evidence)
