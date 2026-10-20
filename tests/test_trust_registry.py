from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import jsonschema
import pytest
from core.machine_identity import MachineIdentity
from core.trust_registry import InvalidTrustBinding, TrustBinding, TrustRegistry, TrustState


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _identity(
    machine_id: str,
    key_id: str,
    fingerprint: str,
    *,
    key_version: int = 1,
    node_class: str = "edge_host",
    transports: frozenset[str] = frozenset({"ssh", "tailscale"}),
    capabilities: frozenset[str] = frozenset({"node.read", "node.control"}),
    verified_at: datetime = NOW - timedelta(days=1),
    expires_at: datetime | None = NOW + timedelta(days=90),
) -> MachineIdentity:
    return MachineIdentity(
        machine_id=machine_id,
        node_class=node_class,
        key_id=key_id,
        key_version=key_version,
        public_fingerprint=fingerprint,
        transport_profiles=transports,
        capabilities=capabilities,
        issued_at=NOW - timedelta(days=60),
        expires_at=expires_at,
        last_verified_at=verified_at,
    )


def _binding(identity: MachineIdentity, state: TrustState = TrustState.TRUSTED) -> TrustBinding:
    return TrustBinding(
        identity=identity,
        trust_state=state,
        allowed_transports=frozenset({"ssh"}),
        allowed_capabilities=frozenset({"node.read"}),
        state_changed_at=NOW - timedelta(days=1),
        rotation_expires_at=NOW + timedelta(hours=6) if state == TrustState.ROTATING else None,
    )


def test_synthetic_enrollment_is_pending_until_promoted() -> None:
    pending = _binding(
        _identity("enroll-node", "key-v1", "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
        TrustState.PENDING,
    )
    registry = TrustRegistry([pending])

    decision = registry.enrollment_state(
        machine_id="enroll-node",
        public_fingerprint=pending.identity.public_fingerprint,
        at=NOW,
    )
    assert not decision.allowed
    assert decision.public_fingerprint == pending.identity.public_fingerprint
    assert decision.reasons == ("IDENTITY_PENDING",)

    enrolled = registry.with_state(
        machine_id="enroll-node",
        key_id="key-v1",
        trust_state=TrustState.TRUSTED,
        changed_at=NOW,
    )
    assert enrolled.authorize(
        machine_id="enroll-node",
        public_fingerprint=pending.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.read",
        at=NOW,
    ).allowed


def test_unknown_revoked_expired_and_stale_identities_fail_closed() -> None:
    revoked = _binding(
        _identity("revoked-node", "key-v1", "SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"),
        TrustState.REVOKED,
    )
    expired = _binding(
        _identity(
            "expired-node",
            "key-v1",
            "SHA256:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
            expires_at=NOW - timedelta(seconds=1),
        )
    )
    stale = _binding(
        _identity(
            "stale-node",
            "key-v1",
            "SHA256:DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
            verified_at=NOW - timedelta(days=31),
        )
    )
    registry = TrustRegistry([revoked, expired, stale])

    unknown = registry.authorize(
        machine_id="missing-node",
        public_fingerprint="SHA256:EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
        transport_profile="ssh",
        capability="node.read",
        at=NOW,
    )
    assert unknown.reasons == ("UNKNOWN_IDENTITY",)
    assert not registry.authorize(
        machine_id="revoked-node",
        public_fingerprint="SHA256:UNKNOWNUNKNOWNUNKNOWNUNKNOWNUNKNOWNUNKNOWNU",
        transport_profile="ssh",
        capability="node.read",
        at=NOW,
    ).allowed
    assert "IDENTITY_REVOKED" in registry.authorize(
        machine_id="revoked-node",
        public_fingerprint=revoked.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.read",
        at=NOW,
    ).reasons
    assert "IDENTITY_EXPIRED" in registry.authorize(
        machine_id="expired-node",
        public_fingerprint=expired.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.read",
        at=NOW,
    ).reasons
    assert "IDENTITY_STALE" in registry.authorize(
        machine_id="stale-node",
        public_fingerprint=stale.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.read",
        at=NOW,
    ).reasons


def test_transport_authentication_does_not_bypass_capability_gate() -> None:
    binding = _binding(_identity("runner-01", "key-v1", "SHA256:FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"))
    registry = TrustRegistry([binding])

    decision = registry.authorize(
        machine_id="runner-01",
        public_fingerprint=binding.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.control",
        at=NOW,
    )

    assert not decision.allowed
    assert decision.reasons == ("CAPABILITY_NOT_ALLOWED",)


def test_constrained_relay_profile_cannot_receive_host_authority() -> None:
    relay = _identity(
        "relay-01",
        "key-v1",
        "SHA256:KKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKKK",
        node_class="constrained_relay",
        transports=frozenset({"relay"}),
        capabilities=frozenset({"node.read", "node.control"}),
    )

    with pytest.raises(InvalidTrustBinding, match="constrained_relay_capability_too_broad"):
        TrustBinding(
            identity=relay,
            trust_state=TrustState.TRUSTED,
            allowed_transports=frozenset({"relay"}),
            allowed_capabilities=frozenset({"node.control"}),
            state_changed_at=NOW,
        )


def test_rotation_overlap_and_independent_revocation() -> None:
    old = _binding(
        _identity("home-edge-01", "key-v1", "SHA256:GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG"),
        TrustState.ROTATING,
    )
    new = _binding(
        _identity(
            "home-edge-01",
            "key-v2",
            "SHA256:HHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHHH",
            key_version=2,
        ),
        TrustState.TRUSTED,
    )
    other = _binding(_identity("runner-01", "key-v1", "SHA256:IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII"))
    registry = TrustRegistry([old, new, other])

    for binding in (old, new):
        assert registry.authorize(
            machine_id="home-edge-01",
            public_fingerprint=binding.identity.public_fingerprint,
            transport_profile="ssh",
            capability="node.read",
            at=NOW,
        ).allowed

    rotated = registry.with_state(
        machine_id="home-edge-01",
        key_id="key-v1",
        trust_state=TrustState.REVOKED,
        changed_at=NOW,
    )
    assert not rotated.authorize(
        machine_id="home-edge-01",
        public_fingerprint=old.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.read",
        at=NOW,
    ).allowed
    assert rotated.authorize(
        machine_id="home-edge-01",
        public_fingerprint=new.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.read",
        at=NOW,
    ).allowed
    assert rotated.authorize(
        machine_id="runner-01",
        public_fingerprint=other.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.read",
        at=NOW,
    ).allowed


def test_rotation_overlap_expires_at_cutover() -> None:
    old = _binding(
        _identity("rotate-node", "key-v1", "SHA256:LLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLLL"),
        TrustState.ROTATING,
    )
    new = _binding(
        _identity(
            "rotate-node",
            "key-v2",
            "SHA256:MMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMMM",
            key_version=2,
        ),
        TrustState.TRUSTED,
    )
    registry = TrustRegistry([old, new])

    assert registry.authorize(
        machine_id="rotate-node",
        public_fingerprint=old.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.read",
        at=NOW + timedelta(hours=5),
    ).allowed
    decision = registry.authorize(
        machine_id="rotate-node",
        public_fingerprint=old.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.read",
        at=NOW + timedelta(hours=6),
    )
    assert not decision.allowed
    assert "ROTATION_OVERLAP_EXPIRED" in decision.reasons


def test_registry_rejects_unstable_key_versions_and_shared_fingerprints() -> None:
    first = _binding(_identity("node-a", "key-v1", "SHA256:NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN"))
    same_version = _binding(
        _identity(
            "node-a",
            "key-v2",
            "SHA256:OOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOOO",
            key_version=1,
        )
    )
    with pytest.raises(InvalidTrustBinding, match="duplicate_machine_key_version"):
        TrustRegistry([first, same_version])

    shared = _binding(_identity("node-b", "key-v1", first.identity.public_fingerprint))
    with pytest.raises(InvalidTrustBinding, match="duplicate_public_fingerprint"):
        TrustRegistry([first, shared])


def test_trust_binding_schema_and_receipt_are_public_safe() -> None:
    identity_schema = json.loads((ROOT / "schemas/machine_identity.schema.json").read_text(encoding="utf-8"))
    trust_schema = json.loads((ROOT / "schemas/trust_binding.schema.json").read_text(encoding="utf-8"))
    receipt_schema = json.loads((ROOT / "schemas/trust_registry_receipt.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(identity_schema)
    jsonschema.Draft202012Validator.check_schema(trust_schema)
    jsonschema.Draft202012Validator.check_schema(receipt_schema)
    resolver = jsonschema.RefResolver.from_schema(
        trust_schema,
        store={
            "https://schemas.skeleton.local/machine_identity.schema.json": identity_schema,
            "machine_identity.schema.json": identity_schema,
        },
    )
    validator = jsonschema.Draft202012Validator(
        trust_schema,
        resolver=resolver,
        format_checker=jsonschema.FormatChecker(),
    )
    binding = _binding(_identity("home-edge-01", "key-v1", "SHA256:JJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJJ"))
    validator.validate(binding.to_mapping())

    decision = TrustRegistry([binding]).authorize(
        machine_id="home-edge-01",
        public_fingerprint=binding.identity.public_fingerprint,
        transport_profile="ssh",
        capability="node.read",
        at=NOW,
    )
    serialized = json.dumps(decision.to_receipt()).lower()
    assert decision.allowed
    assert decision.public_fingerprint == binding.identity.public_fingerprint
    assert "private" not in serialized
    assert "secret" not in serialized
    jsonschema.Draft202012Validator(receipt_schema).validate(decision.to_receipt())
