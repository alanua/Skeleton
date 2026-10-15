"""Offline synthetic Ed25519 acceptance and rejection cases; no real secrets."""
from __future__ import annotations

from dataclasses import replace
import base64
import json
import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from core.workstation_signed_gate import SignedGateVerifier
from core.workstation_agent import WorkstationRejected
from tests.test_workstation_agent import NOW, cmd, agent

ISSUER = "skeleton-gate"
AUDIENCE = "workstation-agent"


def make_pair():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return private, public


def proof_for(private, command, **changes):
    payload = {
        "schema": "skeleton.workstation_signed_grant.v1",
        "issuer": ISSUER, "audience": AUDIENCE, "node_id": command.node_id,
        "node_session_id": command.node_session_id,
        "application_session_id": command.application_session_id,
        "lease_id": command.lease_id, "command_id": command.command_id,
        "operation": command.operation, "fingerprint": command.fingerprint(),
        "not_before": NOW - 10, "expires_at": NOW + 15,
        "approval_reference": "operator-synthetic",
    }
    payload.update(changes)
    sig = private.sign(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=True, allow_nan=False).encode("ascii"))
    payload["signature"] = base64.b64encode(sig).decode("ascii")
    return payload


def verifier(private, public, command, **changes):
    p = proof_for(private, command, **changes)
    return SignedGateVerifier(issuer=ISSUER, audience=AUDIENCE, public_key=public,
                              load_proof=lambda _command: p, clock=lambda: NOW)


def test_verified_grant_binds_exact_command_and_agent_accepts():
    private, public = make_pair()
    c = cmd("APPLY", payload={"entity_id":"wall", "properties":{"height":3}})
    gate = verifier(private, public, c)
    r = gate.authorize(c)
    assert r.approved and r.command_id == c.command_id
    a, db = agent(gate=gate)
    assert a.submit(c).status == "APPLIED"
    assert db.public_snapshot()["revision"] == 1


@pytest.mark.parametrize("alter", [
    {"node_id":"other"}, {"application_session_id":"other"},
    {"lease_id":"other"}, {"operation":"INSPECT"},
    {"fingerprint":"0"*64}, {"audience":"different"}, {"issuer":"other"},
])
def test_forged_or_cross_context_grant_denied(alter):
    private, public = make_pair()
    c = cmd()
    gate = verifier(private, public, c, **alter)
    with pytest.raises(WorkstationRejected):
        gate.authorize(c)


def test_signature_tamper_key_mismatch_and_expiry_denied():
    private, public = make_pair()
    other, _ = make_pair()
    c = cmd()
    p = proof_for(private, c)
    p["approval_reference"] = "tampered"
    gate = SignedGateVerifier(issuer=ISSUER, audience=AUDIENCE, public_key=public,
                              load_proof=lambda _: p, clock=lambda: NOW)
    with pytest.raises(WorkstationRejected, match="TRUST_SIGNATURE_INVALID"):
        gate.authorize(c)
    wrong = verifier(other, public, c)
    with pytest.raises(WorkstationRejected, match="TRUST_SIGNATURE_INVALID"):
        wrong.authorize(c)
    for times in ({"not_before":NOW+1}, {"expires_at":NOW}, {"expires_at": c.expires_at+1}):
        with pytest.raises(WorkstationRejected):
            verifier(private, public, c, **times).authorize(c)


def test_missing_signature_unknown_fields_and_no_loader_fail_closed():
    private, public = make_pair()
    c = cmd()
    p = proof_for(private, c)
    del p["signature"]
    gate = SignedGateVerifier(issuer=ISSUER, audience=AUDIENCE, public_key=public,
                              load_proof=lambda _: p, clock=lambda: NOW)
    with pytest.raises(WorkstationRejected, match="MALFORMED_TRUST_PROOF"):
        gate.authorize(c)
    broken = SignedGateVerifier(issuer=ISSUER, audience=AUDIENCE, public_key=public,
                                load_proof=lambda _: (_ for _ in ()).throw(OSError()),
                                clock=lambda: NOW)
    with pytest.raises(WorkstationRejected, match="TRUST_PROOF_UNAVAILABLE"):
        broken.authorize(c)


def test_agent_must_not_accept_unsigned_grant_or_stale_proof():
    private, public = make_pair()
    c = cmd("APPLY", payload={"entity_id":"wall", "properties":{"x":2}})
    gate = verifier(private, public, c)
    a, db = agent(gate=gate)
    altered = replace(c, payload={"entity_id":"wall", "properties":{"x":3}})
    result = a.submit(altered)
    assert result.reason_class == "GATE_UNAVAILABLE"
    assert db.public_snapshot()["object_count"] == 0


def test_signed_ack_rejects_wrong_node_tamper_and_stale_time():
    from core.workstation_signed_gate import SignedReceiptAckVerifier
    priv, pub = make_pair()
    receipt_hash = "a" * 64
    fields = {
        "schema": "skeleton.workstation_receipt_ack.v1",
        "issuer": ISSUER, "audience": AUDIENCE,
        "node_id": "node-1", "receipt_hash": receipt_hash,
        "not_before": NOW-10, "expires_at": NOW+10,
    }

    def signed(body):
        sig = priv.sign(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True, allow_nan=False).encode("ascii"))
        return json.dumps({**body, "signature": base64.b64encode(sig).decode("ascii")}).encode()

    verifier = SignedReceiptAckVerifier(issuer=ISSUER, audience=AUDIENCE, node_id="node-1",
                                       public_key=pub, clock=lambda: NOW)
    good = signed(fields)
    assert verifier.verify(receipt_hash, good)
    assert not verifier.verify("b"*64, good)
    assert not verifier.verify(receipt_hash, signed({**fields, "node_id":"node-2"}))
    assert not verifier.verify(receipt_hash, signed({**fields, "expires_at":NOW-1}))
    assert not verifier.verify(receipt_hash, good.replace(b'"node-1"',b'"node-2"'))
    assert not verifier.verify(receipt_hash, b'{"invalid":true}')


def test_encrypted_store_accepts_only_signed_ack(tmp_path):
    from core.workstation_signed_gate import SignedReceiptAckVerifier
    from core.workstation_receipt_store import EncryptedReceiptStore
    from core.workstation_agent import CommandReceipt
    priv, pub = make_pair()
    digest = "b" * 64
    fields = {
        "schema":"skeleton.workstation_receipt_ack.v1",
        "issuer": ISSUER, "audience": AUDIENCE, "node_id":"node-1",
        "receipt_hash":digest, "not_before":NOW-1, "expires_at":NOW+30,
    }
    signature = base64.b64encode(priv.sign(
        json.dumps(fields,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("ascii")
    )).decode()
    signed = json.dumps({**fields,"signature":signature}).encode()
    proof = SignedReceiptAckVerifier(issuer=ISSUER,audience=AUDIENCE,node_id="node-1",
                                     public_key=pub,clock=lambda:NOW)
    with EncryptedReceiptStore(path=tmp_path/"data.db",key=b"x"*32,
                                verify_ack=proof.verify) as db:
        db.enqueue(CommandReceipt("APPLIED","OK","APPLY",0,1,digest))
        with pytest.raises(WorkstationRejected):
            db.acknowledge(digest,b"forged")
        assert db.count()==1
        assert db.acknowledge(digest,signed)
        assert db.count()==0
