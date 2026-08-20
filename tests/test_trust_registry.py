from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import jsonschema
from core.machine_identity import MachineIdentity
from core.trust_registry import TrustBinding, TrustRegistry, TrustState


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def _identity(
    machine_id: str,
    key_id: str,
    fingerprint: str,
    *,
    key_version: int = 1,
    verified_at: datetime = NOW - timedelta(days=1),
    expires_at: datetime | None = NOW + timedelta(days=90),
) -> MachineIdentity:
    return MachineIdentity(
        machine_id=machine_id,
        node_class="edge_host",
        key_id=key_id,
        key_version=key_version,
        public_fingerprint=fingerprint,
        transport_profiles=frozenset({"ssh", "tailscale"}),
        capabilities=frozenset({"node.read", "node.control"}),
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
    )


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


def test_trust_binding_schema_and_receipt_are_public_safe() -> None:
    identity_schema = json.loads((ROOT / "schemas/machine_identity.schema.json").read_text(encoding="utf-8"))
    trust_schema = json.loads((ROOT / "schemas/trust_binding.schema.json").read_text(encoding="utf-8"))
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
    assert "private" not in serialized
    assert "secret" not in serialized
