"""Verify server-issued, exact-command CAD authority without possessing signing keys.

Source-only boundary. The trusted server signer, pinned public-key enrollment, and
authenticated outbound transport must be supplied by Skeleton control-plane runtime.
No private key is ever generated, loaded or persisted by this module.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import base64
import json
import math
import re
import time

from core.workstation_agent import CommandEnvelope, GateAuthorization, WorkstationRejected

_SCHEMA = "skeleton.workstation_signed_grant.v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FIELDS = frozenset({
    "schema", "issuer", "audience", "node_id", "node_session_id",
    "application_session_id", "lease_id", "command_id", "operation",
    "fingerprint", "not_before", "expires_at", "approval_reference",
})
_SIG_BYTES = 64


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


class SignedGateVerifier:
    """Fail-closed client-side verifier of pinned Ed25519 operator grants.

    Proof loader is a registered, trusted callback, not request-supplied URL/path.
    All authority fields are checked against the current CommandEnvelope; the
    signature alone never permits wider workspaces, commands, leases or times.
    """

    def __init__(self, *, issuer: str, audience: str, public_key: bytes,
                 load_proof: Callable[[CommandEnvelope], Mapping[str, object]],
                 clock: Callable[[], float] = time.time) -> None:
        if not isinstance(issuer, str) or not _SAFE_ID.fullmatch(issuer):
            raise WorkstationRejected("INVALID_TRUST_ISSUER")
        if not isinstance(audience, str) or not _SAFE_ID.fullmatch(audience):
            raise WorkstationRejected("INVALID_TRUST_AUDIENCE")
        if not isinstance(public_key, bytes) or len(public_key) != 32:
            raise WorkstationRejected("INVALID_PINNED_PUBLIC_KEY")
        if not callable(load_proof) or not callable(clock):
            raise WorkstationRejected("INVALID_TRUST_CALLBACK")
        # Import at the pinned boundary; production packaging must include cryptography.
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            self._verifier = Ed25519PublicKey.from_public_bytes(public_key)
        except Exception as exc:
            raise WorkstationRejected("PINNED_KEY_UNAVAILABLE") from exc
        self._issuer, self._audience = issuer, audience
        self._load_proof, self._clock = load_proof, clock

    def authorize(self, command: CommandEnvelope) -> GateAuthorization:
        if not isinstance(command, CommandEnvelope):
            raise WorkstationRejected("TYPED_COMMAND_REQUIRED")
        try:
            proof = self._load_proof(command)
        except Exception:
            raise WorkstationRejected("TRUST_PROOF_UNAVAILABLE") from None
        if not isinstance(proof, Mapping) or set(proof) != _FIELDS | {"signature"}:
            raise WorkstationRejected("MALFORMED_TRUST_PROOF")
        signed = {k: proof[k] for k in _FIELDS}
        if (signed["schema"] != _SCHEMA or signed["issuer"] != self._issuer
                or signed["audience"] != self._audience):
            raise WorkstationRejected("TRUST_ISSUER_MISMATCH")
        expected = {
            "node_id": command.node_id,
            "node_session_id": command.node_session_id,
            "application_session_id": command.application_session_id,
            "lease_id": command.lease_id,
            "command_id": command.command_id,
            "operation": command.operation,
            "fingerprint": command.fingerprint(),
        }
        if any(signed[k] != v for k, v in expected.items()):
            raise WorkstationRejected("TRUST_COMMAND_MISMATCH")
        if (not isinstance(signed["approval_reference"], str)
                or not _SAFE_ID.fullmatch(signed["approval_reference"])):
            raise WorkstationRejected("INVALID_TRUST_REFERENCE")
        now = self._clock()
        start, end = signed["not_before"], signed["expires_at"]
        if (not isinstance(now, (float, int)) or not math.isfinite(now)
                or type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or start >= end or now < start or now >= end
                or end > command.expires_at):
            raise WorkstationRejected("TRUST_EXPIRED_OR_FUTURE")
        if not isinstance(proof["signature"], str):
            raise WorkstationRejected("INVALID_TRUST_SIGNATURE")
        try:
            signature = base64.b64decode(proof["signature"], validate=True)
            if len(signature) != _SIG_BYTES:
                raise ValueError("invalid signature length")
            self._verifier.verify(signature, _canonical(signed))
        except Exception:
            raise WorkstationRejected("TRUST_SIGNATURE_INVALID") from None
        return GateAuthorization(
            approved=True, node_id=command.node_id,
            node_session_id=command.node_session_id,
            lease_id=command.lease_id, command_id=command.command_id,
            operation=command.operation, expires_at=float(end),
            approval_reference=signed["approval_reference"],
        )


class SignedReceiptAckVerifier:
    """Fixed-server Ed25519 acknowledgement verifier for encrypted receipt deletion.

    The consumer receives only an exact hash plus signed public-safe envelope.
    No user-caller-selected issuer, node, transport, certificate or destination.
    """

    _ACK_SCHEMA = "skeleton.workstation_receipt_ack.v1"
    _ACK_FIELDS = frozenset({
        "schema", "issuer", "audience", "node_id", "receipt_hash",
        "not_before", "expires_at",
    })

    def __init__(self, *, issuer: str, audience: str, node_id: str,
                 public_key: bytes, clock: Callable[[], float] = time.time) -> None:
        if not all(isinstance(x, str) and _SAFE_ID.fullmatch(x)
                   for x in (issuer, audience, node_id)):
            raise WorkstationRejected("INVALID_ACK_IDENTITY")
        if not isinstance(public_key, bytes) or len(public_key) != 32:
            raise WorkstationRejected("INVALID_ACK_PUBLIC_KEY")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        self._verifier = Ed25519PublicKey.from_public_bytes(public_key)
        self._issuer, self._audience, self._node_id = issuer, audience, node_id
        self._clock = clock

    def verify(self, receipt_hash: str, signed_ack: bytes) -> bool:
        if (not isinstance(receipt_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", receipt_hash)
                or not isinstance(signed_ack, bytes) or len(signed_ack) > 4096):
            return False
        try:
            proof = json.loads(signed_ack)
            if not isinstance(proof, dict) or set(proof) != self._ACK_FIELDS | {"signature"}:
                return False
            signed = {k: proof[k] for k in self._ACK_FIELDS}
            if (signed["schema"] != self._ACK_SCHEMA or signed["issuer"] != self._issuer
                    or signed["audience"] != self._audience or signed["node_id"] != self._node_id
                    or signed["receipt_hash"] != receipt_hash):
                return False
            now = self._clock()
            start, end = signed["not_before"], signed["expires_at"]
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not all(math.isfinite(v) for v in (now, start, end))
                    or not start <= now < end or end - start > 900):
                return False
            sig = base64.b64decode(proof["signature"], validate=True)
            if len(sig) != _SIG_BYTES:
                return False
            self._verifier.verify(sig, _canonical(signed))
            return True
        except Exception:
            return False
