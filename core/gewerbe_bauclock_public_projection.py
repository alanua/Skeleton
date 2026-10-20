from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping


GEWERBE_BAUCLOCK_PUBLIC_PROJECTION_SOURCE_SCHEMA = (
    "skeleton.finance.gewerbe_bauclock_public_projection_source.v1"
)
GEWERBE_BAUCLOCK_PUBLIC_PROJECTION_SCHEMA = "skeleton.finance.gewerbe_bauclock_public_projection.v1"
GEWERBE_NAMESPACE = "gewerbe"
BAUCLOCK_SOURCE_SYSTEM = "bauclock"

MAX_CANONICAL_REVISION = 1_000_000_000
MAX_AGGREGATE_RECORDS = 256
MAX_COUNT = 1_000_000

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_BUCKET_RE = re.compile(r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
_UTC_TIMESTAMP_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

_SOURCE_KEYS = frozenset(
    {
        "schema",
        "namespace",
        "source_system",
        "canonical_ref",
        "canonical_revision",
        "source_hash",
        "freshness",
        "aggregate_records",
    }
)
_FRESHNESS_KEYS = frozenset({"generated_at", "fresh_until", "stale"})
_RECORD_KEYS = frozenset(
    {
        "period_bucket",
        "category",
        "status",
        "record_count",
        "document_link_count",
        "source_hash",
    }
)
_PRIVATE_KEY_TOKENS = frozenset(
    {
        "amount",
        "balance",
        "account",
        "iban",
        "bic",
        "tax",
        "vat",
        "steuernummer",
        "ustid",
        "name",
        "text",
        "email",
        "address",
        "person",
        "identity",
        "employee",
        "worker",
        "user",
    }
)


class GewerbeBauclockPublicProjectionError(ValueError):
    """Raised when a Gewerbe/BauClock projection source is not public-safe."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class PublicAggregateRecord:
    period_bucket: str
    category: str
    status: str
    record_count: int
    document_link_count: int
    source_hash: str


@dataclass(frozen=True)
class PublicProjectionSource:
    namespace: str
    source_system: str
    canonical_ref: str
    canonical_revision: int
    source_hash: str
    generated_at: str
    fresh_until: str
    stale: bool
    aggregate_records: tuple[PublicAggregateRecord, ...]


def build_public_projection(source: Mapping[str, Any]) -> dict[str, object]:
    """Build a deterministic public-safe aggregate projection from aggregate-only source data."""

    loaded = load_public_projection_source(source)
    buckets: dict[str, dict[str, object]] = {}
    source_hashes = {loaded.source_hash}

    for record in loaded.aggregate_records:
        bucket = buckets.setdefault(
            record.period_bucket,
            {
                "period_bucket": record.period_bucket,
                "category_counts": {},
                "status_counts": {},
                "record_count": 0,
                "document_link_count": 0,
                "source_hashes": set(),
            },
        )
        _add_count(bucket["category_counts"], record.category, record.record_count)
        _add_count(bucket["status_counts"], record.status, record.record_count)
        bucket["record_count"] = int(bucket["record_count"]) + record.record_count
        bucket["document_link_count"] = int(bucket["document_link_count"]) + record.document_link_count
        bucket["source_hashes"].add(record.source_hash)  # type: ignore[union-attr]
        source_hashes.add(record.source_hash)

    period_buckets = []
    for bucket in sorted(buckets.values(), key=lambda item: str(item["period_bucket"])):
        period_buckets.append(
            {
                "period_bucket": bucket["period_bucket"],
                "category_counts": dict(sorted(dict(bucket["category_counts"]).items())),
                "status_counts": dict(sorted(dict(bucket["status_counts"]).items())),
                "record_count": bucket["record_count"],
                "document_link_count": bucket["document_link_count"],
                "source_hashes": sorted(bucket["source_hashes"]),
            }
        )

    projection: dict[str, object] = {
        "schema": GEWERBE_BAUCLOCK_PUBLIC_PROJECTION_SCHEMA,
        "namespace": loaded.namespace,
        "source_system": loaded.source_system,
        "canonical_ref": loaded.canonical_ref,
        "canonical_revision": loaded.canonical_revision,
        "source_hash": loaded.source_hash,
        "freshness": {
            "generated_at": loaded.generated_at,
            "fresh_until": loaded.fresh_until,
            "stale": loaded.stale,
        },
        "aggregate_counts": {
            "period_bucket_count": len(period_buckets),
            "record_count": sum(int(bucket["record_count"]) for bucket in period_buckets),
            "document_link_count": sum(int(bucket["document_link_count"]) for bucket in period_buckets),
        },
        "period_buckets": period_buckets,
        "source_hashes": sorted(source_hashes),
        "authoritative": False,
    }
    projection["projection_hash"] = _canonical_hash(projection)
    return projection


def load_public_projection_source(source: Mapping[str, Any]) -> PublicProjectionSource:
    if not isinstance(source, Mapping):
        raise GewerbeBauclockPublicProjectionError("INVALID_SOURCE", "projection source must be an object")
    _reject_private_fields(source)
    _reject_extra_keys(source, _SOURCE_KEYS, "source")
    if source.get("schema") != GEWERBE_BAUCLOCK_PUBLIC_PROJECTION_SOURCE_SCHEMA:
        raise GewerbeBauclockPublicProjectionError("INVALID_SOURCE_SCHEMA", "projection source schema is invalid")
    namespace = _const(source.get("namespace"), GEWERBE_NAMESPACE, "namespace")
    source_system = _const(source.get("source_system"), BAUCLOCK_SOURCE_SYSTEM, "source_system")
    canonical_ref = _safe_token(source.get("canonical_ref"), "canonical_ref")
    canonical_revision = _bounded_int(source.get("canonical_revision"), "canonical_revision", minimum=1)
    source_hash = _sha256(source.get("source_hash"), "source_hash")

    freshness = source.get("freshness")
    if not isinstance(freshness, Mapping):
        raise GewerbeBauclockPublicProjectionError("INVALID_FRESHNESS", "freshness must be an object")
    _reject_extra_keys(freshness, _FRESHNESS_KEYS, "freshness")
    generated_at = _timestamp(freshness.get("generated_at"), "generated_at")
    fresh_until = _timestamp(freshness.get("fresh_until"), "fresh_until")
    stale = freshness.get("stale")
    if not isinstance(stale, bool):
        raise GewerbeBauclockPublicProjectionError("INVALID_STALE_FLAG", "stale must be boolean")

    raw_records = source.get("aggregate_records")
    if not isinstance(raw_records, list) or not raw_records:
        raise GewerbeBauclockPublicProjectionError("AGGREGATE_RECORDS_REQUIRED", "aggregate_records must be non-empty")
    if len(raw_records) > MAX_AGGREGATE_RECORDS:
        raise GewerbeBauclockPublicProjectionError("AGGREGATE_RECORD_LIMIT_EXCEEDED", "too many aggregate records")

    records = tuple(_load_record(record) for record in raw_records)
    return PublicProjectionSource(
        namespace=namespace,
        source_system=source_system,
        canonical_ref=canonical_ref,
        canonical_revision=canonical_revision,
        source_hash=source_hash,
        generated_at=generated_at,
        fresh_until=fresh_until,
        stale=stale,
        aggregate_records=records,
    )


def _load_record(value: object) -> PublicAggregateRecord:
    if not isinstance(value, Mapping):
        raise GewerbeBauclockPublicProjectionError("INVALID_AGGREGATE_RECORD", "aggregate record must be an object")
    _reject_extra_keys(value, _RECORD_KEYS, "aggregate_record")
    return PublicAggregateRecord(
        period_bucket=_period_bucket(value.get("period_bucket")),
        category=_safe_token(value.get("category"), "category"),
        status=_safe_token(value.get("status"), "status"),
        record_count=_bounded_int(value.get("record_count"), "record_count", minimum=0),
        document_link_count=_bounded_int(value.get("document_link_count"), "document_link_count", minimum=0),
        source_hash=_sha256(value.get("source_hash"), "source_hash"),
    )


def _reject_private_fields(value: object, path: str = "source") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            tokens = {token for token in re.split(r"[^a-z0-9]+", key_text.lower()) if token}
            if tokens & _PRIVATE_KEY_TOKENS:
                raise GewerbeBauclockPublicProjectionError(
                    "PRIVATE_FIELD_REJECTED",
                    f"{path} contains private finance or BauClock identity field",
                )
            _reject_private_fields(nested, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_private_fields(nested, f"{path}[{index}]")


def _reject_extra_keys(data: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
    extra = sorted(set(data) - set(allowed))
    if extra:
        raise GewerbeBauclockPublicProjectionError(
            "UNSUPPORTED_PUBLIC_PROJECTION_FIELD",
            f"{path} contains unsupported field",
        )


def _const(value: object, expected: str, name: str) -> str:
    if value != expected:
        raise GewerbeBauclockPublicProjectionError("INVALID_SCOPE", f"{name} is outside projection scope")
    return expected


def _safe_token(value: object, name: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise GewerbeBauclockPublicProjectionError("INVALID_TOKEN", f"{name} must be a bounded token")
    return value


def _period_bucket(value: object) -> str:
    if not isinstance(value, str) or _SAFE_BUCKET_RE.fullmatch(value) is None:
        raise GewerbeBauclockPublicProjectionError("INVALID_PERIOD_BUCKET", "period_bucket must be YYYY-MM")
    return value


def _timestamp(value: object, name: str) -> str:
    if not isinstance(value, str) or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise GewerbeBauclockPublicProjectionError("INVALID_TIMESTAMP", f"{name} must be a UTC timestamp")
    return value


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise GewerbeBauclockPublicProjectionError("INVALID_HASH", f"{name} must be lowercase sha256 hex")
    return value


def _bounded_int(value: object, name: str, *, minimum: int) -> int:
    maximum = MAX_CANONICAL_REVISION if name == "canonical_revision" else MAX_COUNT
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise GewerbeBauclockPublicProjectionError("INVALID_INTEGER", f"{name} is outside allowed bounds")
    return value


def _add_count(target: object, key: str, count: int) -> None:
    counts = target
    if not isinstance(counts, dict):
        raise GewerbeBauclockPublicProjectionError("INVALID_AGGREGATE_STATE", "aggregate state is invalid")
    counts[key] = int(counts.get(key, 0)) + count


def _canonical_hash(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
