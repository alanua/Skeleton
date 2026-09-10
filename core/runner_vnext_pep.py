from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from core.runner_vnext_adapters import ExecutionEnvelope
from core.runner_vnext_contracts import EffectClass


class PEPError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LedgerReservationEvidence:
    operation_id: str
    idempotency_key: str
    target_state_ref: str
    fence_token: int
    status: str
    envelope_fingerprint: str


@dataclass(frozen=True)
class AuthorityClaim:
    evidence_refs: tuple[str, ...]
    operation_id: str
    target_state_ref: str
    effect_class: EffectClass
    resources: tuple[str, ...]
    fresh: bool


class AuthorityVerifier(Protocol):
    def verify(self, claim: AuthorityClaim) -> bool: ...


@dataclass(frozen=True)
class PEPAuthorizationReceipt:
    adapter_id: str
    operation_id: str
    target_state_ref: str
    fence_token: int
    idempotency_key: str
    effect_class: EffectClass
    policy_reason_code: str
    authorization_status: str
    side_effects_executed: bool = False


@dataclass(frozen=True)
class PrivilegedBrokerRequest:
    adapter_id: str
    operation_id: str
    target_state_ref: str
    fence_token: int
    idempotency_key: str
    resources: tuple[str, ...]
    effects: tuple[str, ...]
    effect_class: EffectClass
    policy_reason_code: str
    approval_evidence_refs: tuple[str, ...]
    execution_authorized: bool = False


class PolicyEnforcementPoint:
    """Pure authorization boundary. It never invokes adapters or privileged actions."""

    def evaluate(
        self,
        envelope: ExecutionEnvelope,
        ledger: LedgerReservationEvidence,
        *,
        authority_claim: AuthorityClaim | None = None,
        authority_verifier: AuthorityVerifier | None = None,
    ) -> PEPAuthorizationReceipt | PrivilegedBrokerRequest:
        _validate_envelope(envelope)
        _validate_ledger(envelope, ledger)
        if envelope.effect_class is EffectClass.GREEN:
            if authority_claim is not None:
                raise PEPError("PEP_GREEN_AUTHORITY_CLAIM_UNEXPECTED")
            return PEPAuthorizationReceipt(
                adapter_id=envelope.adapter_id,
                operation_id=envelope.operation_id,
                target_state_ref=envelope.target_state_ref,
                fence_token=envelope.fence_token,
                idempotency_key=envelope.idempotency_key,
                effect_class=envelope.effect_class,
                policy_reason_code=envelope.policy_reason_code,
                authorization_status="AUTHORIZED_GREEN_BOUNDED",
            )
        if authority_claim is None or authority_verifier is None:
            raise PEPError("PEP_OPERATOR_AUTHORITY_REQUIRED")
        _validate_authority_binding(envelope, authority_claim)
        if envelope.effect_class is EffectClass.RED and not authority_claim.fresh:
            raise PEPError("PEP_FRESH_OPERATOR_AUTHORITY_REQUIRED")
        if not authority_verifier.verify(authority_claim):
            raise PEPError("PEP_AUTHORITY_VERIFICATION_FAILED")
        return PrivilegedBrokerRequest(
            adapter_id=envelope.adapter_id,
            operation_id=envelope.operation_id,
            target_state_ref=envelope.target_state_ref,
            fence_token=envelope.fence_token,
            idempotency_key=envelope.idempotency_key,
            resources=envelope.resources,
            effects=envelope.effects,
            effect_class=envelope.effect_class,
            policy_reason_code=envelope.policy_reason_code,
            approval_evidence_refs=authority_claim.evidence_refs,
        )


def _validate_envelope(envelope: ExecutionEnvelope) -> None:
    if not envelope.dry_run:
        raise PEPError("PEP_DRY_RUN_ENVELOPE_REQUIRED")
    for value in (envelope.adapter_id, envelope.operation_id, envelope.target_state_ref, envelope.idempotency_key):
        if not _public_ref(value):
            raise PEPError("PEP_PUBLIC_SAFE_REF_REQUIRED")
    if not envelope.policy_reason_code or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in envelope.policy_reason_code):
        raise PEPError("PEP_POLICY_REASON_CODE_INVALID")
    if envelope.fence_token < 1:
        raise PEPError("PEP_FENCE_INVALID")
    if not envelope.resources or not envelope.effects:
        raise PEPError("PEP_BOUNDED_SCOPE_REQUIRED")
    for ref in envelope.resources:
        if not _public_ref(ref):
            raise PEPError("PEP_PUBLIC_SAFE_REF_REQUIRED")


def _validate_ledger(envelope: ExecutionEnvelope, ledger: LedgerReservationEvidence) -> None:
    if ledger.status != "RESERVED":
        raise PEPError("PEP_LEDGER_RESERVATION_REQUIRED")
    if ledger.operation_id != envelope.operation_id:
        raise PEPError("PEP_LEDGER_OPERATION_MISMATCH")
    if ledger.idempotency_key != envelope.idempotency_key:
        raise PEPError("PEP_LEDGER_IDEMPOTENCY_MISMATCH")
    if ledger.target_state_ref != envelope.target_state_ref:
        raise PEPError("PEP_LEDGER_TARGET_STATE_MISMATCH")
    if ledger.fence_token != envelope.fence_token:
        raise PEPError("PEP_LEDGER_FENCE_MISMATCH")
    if ledger.envelope_fingerprint != envelope_fingerprint(envelope):
        raise PEPError("PEP_LEDGER_ENVELOPE_FINGERPRINT_MISMATCH")


def _validate_authority_binding(envelope: ExecutionEnvelope, claim: AuthorityClaim) -> None:
    if not claim.evidence_refs:
        raise PEPError("PEP_APPROVAL_EVIDENCE_REQUIRED")
    for ref in claim.evidence_refs:
        if not _public_ref(ref):
            raise PEPError("PEP_APPROVAL_EVIDENCE_INVALID")
    if claim.operation_id != envelope.operation_id:
        raise PEPError("PEP_AUTHORITY_OPERATION_MISMATCH")
    if claim.target_state_ref != envelope.target_state_ref:
        raise PEPError("PEP_AUTHORITY_TARGET_STATE_MISMATCH")
    if claim.effect_class is not envelope.effect_class:
        raise PEPError("PEP_AUTHORITY_EFFECT_CLASS_MISMATCH")
    if claim.resources != envelope.resources:
        raise PEPError("PEP_AUTHORITY_SCOPE_MISMATCH")


def _public_ref(value: str) -> bool:
    return bool(value) and not value.startswith(("/", "~")) and "\\" not in value and ".." not in value and ":" in value and not any(ch.isspace() for ch in value)


def envelope_fingerprint(envelope: ExecutionEnvelope) -> str:
    payload = {
        "adapter_id": envelope.adapter_id,
        "operation_id": envelope.operation_id,
        "target_state_ref": envelope.target_state_ref,
        "fence_token": envelope.fence_token,
        "idempotency_key": envelope.idempotency_key,
        "resources": list(envelope.resources),
        "effects": list(envelope.effects),
        "effect_class": envelope.effect_class.value,
        "policy_reason_code": envelope.policy_reason_code,
        "required_capabilities": list(envelope.required_capabilities),
        "privacy": envelope.privacy.value,
        "dry_run": envelope.dry_run,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
