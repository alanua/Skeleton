from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import jsonschema
import pytest

from core.topology_fact import TOPOLOGY_FACT_SCHEMA, InvalidTopologyFact, TopologyFact


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def _fact(**overrides: object) -> TopologyFact:
    values = {
        "schema": TOPOLOGY_FACT_SCHEMA,
        "fact_id": "topology:host:home-edge-current",
        "fact_type": "host",
        "lookup_key": "home-edge",
        "value_class": "MACHINE_ID",
        "value_ref": "machine:home-edge-01",
        "source": "operator-attestation",
        "provenance_ref": "receipt:topology:20260821",
        "verified_revision": 3,
        "verified_at": NOW,
        "freshness_class": "CURRENT",
        "fresh_until": datetime(2026, 9, 21, tzinfo=timezone.utc),
        "authority": 50,
        "status": "VERIFIED",
        "roles": frozenset({"edge_host", "media_node"}),
        "public_fingerprints": frozenset({"SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}),
        "supersedes": (),
        "superseded_by": None,
    }
    values.update(overrides)
    return TopologyFact(**values)


def test_topology_fact_round_trip_contains_public_safe_metadata_only() -> None:
    mapping = _fact().to_mapping()

    assert TopologyFact.from_mapping(mapping) == _fact()
    serialized = json.dumps(mapping, sort_keys=True).lower()
    assert "/home/" not in serialized
    assert "192.168." not in serialized
    assert "private_key" not in serialized
    assert "secret" not in serialized


def test_opaque_private_reference_preserves_reference_without_private_value() -> None:
    fact = _fact(
        fact_id="topology:entrypoint:private-root-ref",
        fact_type="entrypoint",
        lookup_key="skeleton-root",
        value_class="OPAQUE_PRIVATE_REF",
        value_ref="private-ref:workspace-root:v1",
        roles=frozenset(),
        public_fingerprints=frozenset(),
    )

    assert fact.is_private_reference is True
    assert fact.to_mapping()["value_ref"] == "private-ref:workspace-root:v1"


def test_topology_fact_rejects_unknown_or_private_fields() -> None:
    mapping = _fact().to_mapping()
    mapping["private_path"] = "PRIVATE_ROOT_VALUE"

    with pytest.raises(InvalidTopologyFact, match="fact_fields_mismatch"):
        TopologyFact.from_mapping(mapping)


def test_stale_freshness_class_is_not_fresh_even_before_fresh_until() -> None:
    fact = _fact(freshness_class="STALE")

    assert fact.is_fresh_at(NOW) is False


def test_topology_fact_schema_validates_and_rejects_extra_fields() -> None:
    schema = json.loads((ROOT / "schemas/topology_fact.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    validator.validate(_fact().to_mapping())

    invalid = _fact().to_mapping()
    invalid["private_path"] = "PRIVATE_ROOT_VALUE"
    assert list(validator.iter_errors(invalid))
