from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import jsonschema
import pytest

from core.machine_identity import InvalidMachineIdentity, MachineIdentity


ROOT = Path(__file__).resolve().parents[1]


def _identity() -> MachineIdentity:
    return MachineIdentity(
        machine_id="home-edge-01",
        node_class="edge_host",
        key_id="ssh-ed25519-v1",
        key_version=1,
        public_fingerprint="SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        transport_profiles=frozenset({"ssh", "tailscale"}),
        capabilities=frozenset({"home_edge.read", "home_edge.control"}),
        issued_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 8, 1, tzinfo=timezone.utc),
        last_verified_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )


def test_machine_identity_round_trip_contains_public_metadata_only() -> None:
    identity = _identity()
    mapping = identity.to_mapping()

    assert MachineIdentity.from_mapping(mapping) == identity
    serialized = json.dumps(mapping).lower()
    assert "private_key" not in serialized
    assert "secret" not in serialized


def test_machine_identity_rejects_unknown_or_secret_fields() -> None:
    mapping = _identity().to_mapping()
    mapping["private_key"] = "forbidden"
    with pytest.raises(InvalidMachineIdentity, match="identity_fields_mismatch"):
        MachineIdentity.from_mapping(mapping)


def test_machine_identity_schema_validates_public_contract_and_rejects_extra_fields() -> None:
    schema = json.loads((ROOT / "schemas/machine_identity.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    validator.validate(_identity().to_mapping())

    invalid = _identity().to_mapping()
    invalid["private_key"] = "forbidden"
    assert list(validator.iter_errors(invalid))
