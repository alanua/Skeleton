from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from core.gewerbe_bauclock_public_projection import (
    BAUCLOCK_SOURCE_SYSTEM,
    GEWERBE_BAUCLOCK_PUBLIC_PROJECTION_SCHEMA,
    GEWERBE_BAUCLOCK_PUBLIC_PROJECTION_SOURCE_SCHEMA,
    GEWERBE_NAMESPACE,
    GewerbeBauclockPublicProjectionError,
    build_public_projection,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "gewerbe_bauclock_public_projection.schema.json"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def aggregate_source() -> dict[str, object]:
    return {
        "schema": GEWERBE_BAUCLOCK_PUBLIC_PROJECTION_SOURCE_SCHEMA,
        "namespace": GEWERBE_NAMESPACE,
        "source_system": BAUCLOCK_SOURCE_SYSTEM,
        "canonical_ref": "gewerbe.bauclock.public.aggregate.2026-08",
        "canonical_revision": 7,
        "source_hash": HASH_A,
        "freshness": {
            "generated_at": "2026-08-15T10:00:00Z",
            "fresh_until": "2026-08-16T10:00:00Z",
            "stale": False,
        },
        "aggregate_records": [
            {
                "period_bucket": "2026-08",
                "category": "invoice",
                "status": "open",
                "record_count": 2,
                "document_link_count": 3,
                "source_hash": HASH_B,
            },
            {
                "period_bucket": "2026-08",
                "category": "expense",
                "status": "open",
                "record_count": 1,
                "document_link_count": 0,
                "source_hash": HASH_C,
            },
            {
                "period_bucket": "2026-07",
                "category": "invoice",
                "status": "closed",
                "record_count": 4,
                "document_link_count": 2,
                "source_hash": HASH_B,
            },
        ],
    }


def test_public_projection_schema_validates_derived_output() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    projection = build_public_projection(aggregate_source())
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(projection)
    assert projection["schema"] == GEWERBE_BAUCLOCK_PUBLIC_PROJECTION_SCHEMA
    assert projection["authoritative"] is False


def test_projection_is_deterministically_normalized_for_same_revision() -> None:
    source = aggregate_source()
    reversed_source = deepcopy(source)
    reversed_source["aggregate_records"] = list(reversed(source["aggregate_records"]))  # type: ignore[arg-type]

    first = build_public_projection(source)
    second = build_public_projection(reversed_source)

    assert first == second
    assert first["canonical_revision"] == 7
    assert first["aggregate_counts"] == {
        "period_bucket_count": 2,
        "record_count": 7,
        "document_link_count": 5,
    }
    assert [bucket["period_bucket"] for bucket in first["period_buckets"]] == ["2026-07", "2026-08"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", 1200),
        ("account_id", "acct-123"),
        ("tax_id", "tax-123"),
        ("customer_name", "Private Person"),
        ("document_text", "invoice body"),
        ("email", "person@example.invalid"),
        ("address", "Street 1"),
        ("person_id", "person-1"),
        ("bauclock_user_id", "worker-1"),
    ],
)
def test_private_fields_are_explicitly_rejected(field: str, value: object) -> None:
    source = aggregate_source()
    source[field] = value

    with pytest.raises(GewerbeBauclockPublicProjectionError) as excinfo:
        build_public_projection(source)

    assert excinfo.value.reason_code == "PRIVATE_FIELD_REJECTED"


def test_nested_private_fields_are_rejected_before_projection() -> None:
    source = aggregate_source()
    records = source["aggregate_records"]
    assert isinstance(records, list)
    records[0]["gross_amount"] = 99

    with pytest.raises(GewerbeBauclockPublicProjectionError) as excinfo:
        build_public_projection(source)

    assert excinfo.value.reason_code == "PRIVATE_FIELD_REJECTED"


def test_unknown_fields_do_not_get_copied_into_public_projection() -> None:
    source = aggregate_source()
    source["raw_value"] = "private literal"

    with pytest.raises(GewerbeBauclockPublicProjectionError) as excinfo:
        build_public_projection(source)

    assert excinfo.value.reason_code == "UNSUPPORTED_PUBLIC_PROJECTION_FIELD"


def test_raw_private_values_are_not_copied_to_output() -> None:
    projection = build_public_projection(aggregate_source())
    encoded = json.dumps(projection, sort_keys=True)

    assert "1200" not in encoded
    assert "acct-" not in encoded
    assert "tax-" not in encoded
    assert "person@example" not in encoded
    assert set(projection) == {
        "schema",
        "namespace",
        "source_system",
        "canonical_ref",
        "canonical_revision",
        "source_hash",
        "freshness",
        "aggregate_counts",
        "period_buckets",
        "source_hashes",
        "authoritative",
        "projection_hash",
    }


def test_revision_freshness_and_hash_fields_are_typed_and_bounded() -> None:
    for field, value, reason in (
        ("canonical_revision", 0, "INVALID_INTEGER"),
        ("source_hash", "A" * 64, "INVALID_HASH"),
    ):
        source = aggregate_source()
        source[field] = value
        with pytest.raises(GewerbeBauclockPublicProjectionError) as excinfo:
            build_public_projection(source)
        assert excinfo.value.reason_code == reason

    source = aggregate_source()
    source["freshness"]["generated_at"] = "2026-08-15"  # type: ignore[index]
    with pytest.raises(GewerbeBauclockPublicProjectionError) as excinfo:
        build_public_projection(source)
    assert excinfo.value.reason_code == "INVALID_TIMESTAMP"
