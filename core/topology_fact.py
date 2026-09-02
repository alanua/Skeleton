from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Mapping


TOPOLOGY_FACT_SCHEMA = "skeleton.topology_fact.v1"
TOPOLOGY_FACT_TYPES = frozenset({"host", "repository", "runtime", "entrypoint"})
TOPOLOGY_FACT_STATUSES = frozenset({"VERIFIED", "REVOKED"})
TOPOLOGY_FRESHNESS_CLASSES = frozenset({"CURRENT", "STALE"})
TOPOLOGY_VALUE_CLASSES = frozenset(
    {"MACHINE_ID", "PROJECT_ID", "RUNTIME_ID", "ENTRYPOINT_ID", "PUBLIC_FINGERPRINT", "OPAQUE_PRIVATE_REF"}
)

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{1,127}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/+~=-]{2,255}$")


class InvalidTopologyFact(ValueError):
    pass


def _require_token(name: str, value: object) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise InvalidTopologyFact(f"invalid_{name}")
    return value


def _require_ref(name: str, value: object) -> str:
    if not isinstance(value, str) or not _REF_RE.fullmatch(value):
        raise InvalidTopologyFact(f"invalid_{name}")
    return value


def _require_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTopologyFact(f"invalid_{name}")
    return value.astimezone(timezone.utc)


def _parse_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise InvalidTopologyFact(f"invalid_{name}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidTopologyFact(f"invalid_{name}") from exc
    return _require_datetime(name, parsed)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_string_set(name: str, value: object) -> frozenset[str]:
    if not isinstance(value, (tuple, list, set, frozenset)):
        raise InvalidTopologyFact(f"invalid_{name}")
    result = frozenset(_require_token(name, item) for item in value)
    if len(result) != len(value):
        raise InvalidTopologyFact(f"duplicate_{name}")
    return result


def _require_string_tuple(name: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise InvalidTopologyFact(f"invalid_{name}")
    result = tuple(_require_token(name, item) for item in value)
    if len(set(result)) != len(result):
        raise InvalidTopologyFact(f"duplicate_{name}")
    return result


@dataclass(frozen=True)
class TopologyFact:
    schema: str
    fact_id: str
    fact_type: str
    lookup_key: str
    value_class: str
    value_ref: str
    source: str
    provenance_ref: str
    verified_revision: int
    verified_at: datetime
    freshness_class: str
    fresh_until: datetime | None
    authority: int
    status: str = "VERIFIED"
    roles: frozenset[str] = frozenset()
    public_fingerprints: frozenset[str] = frozenset()
    supersedes: tuple[str, ...] = ()
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        if self.schema != TOPOLOGY_FACT_SCHEMA:
            raise InvalidTopologyFact("invalid_schema")
        object.__setattr__(self, "fact_id", _require_token("fact_id", self.fact_id))
        if self.fact_type not in TOPOLOGY_FACT_TYPES:
            raise InvalidTopologyFact("invalid_fact_type")
        object.__setattr__(self, "lookup_key", _require_token("lookup_key", self.lookup_key))
        if self.value_class not in TOPOLOGY_VALUE_CLASSES:
            raise InvalidTopologyFact("invalid_value_class")
        object.__setattr__(self, "value_ref", _require_ref("value_ref", self.value_ref))
        object.__setattr__(self, "source", _require_token("source", self.source))
        object.__setattr__(self, "provenance_ref", _require_ref("provenance_ref", self.provenance_ref))
        if not isinstance(self.verified_revision, int) or isinstance(self.verified_revision, bool) or self.verified_revision < 1:
            raise InvalidTopologyFact("invalid_verified_revision")
        object.__setattr__(self, "verified_at", _require_datetime("verified_at", self.verified_at))
        if self.freshness_class not in TOPOLOGY_FRESHNESS_CLASSES:
            raise InvalidTopologyFact("invalid_freshness_class")
        fresh_until = None if self.fresh_until is None else _require_datetime("fresh_until", self.fresh_until)
        if fresh_until is not None and fresh_until < self.verified_at:
            raise InvalidTopologyFact("freshness_precedes_verification")
        object.__setattr__(self, "fresh_until", fresh_until)
        if not isinstance(self.authority, int) or isinstance(self.authority, bool) or self.authority < 0:
            raise InvalidTopologyFact("invalid_authority")
        if self.status not in TOPOLOGY_FACT_STATUSES:
            raise InvalidTopologyFact("invalid_status")
        object.__setattr__(self, "roles", _require_string_set("roles", self.roles))
        object.__setattr__(
            self,
            "public_fingerprints",
            frozenset(_require_ref("public_fingerprints", item) for item in self.public_fingerprints),
        )
        object.__setattr__(self, "supersedes", _require_string_tuple("supersedes", self.supersedes))
        if self.superseded_by is not None:
            object.__setattr__(self, "superseded_by", _require_token("superseded_by", self.superseded_by))
        if self.fact_id in self.supersedes or self.superseded_by_is_self():
            raise InvalidTopologyFact("self_supersession")

    def superseded_by_is_self(self) -> bool:
        return self.superseded_by == self.fact_id

    @property
    def is_private_reference(self) -> bool:
        return self.value_class == "OPAQUE_PRIVATE_REF"

    def is_fresh_at(self, now: datetime) -> bool:
        checked_at = _require_datetime("now", now)
        if self.freshness_class != "CURRENT":
            return False
        return self.fresh_until is None or checked_at <= self.fresh_until

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "TopologyFact":
        if not isinstance(mapping, Mapping):
            raise InvalidTopologyFact("fact_must_be_mapping")
        expected = {
            "schema",
            "fact_id",
            "fact_type",
            "lookup_key",
            "value_class",
            "value_ref",
            "source",
            "provenance_ref",
            "verified_revision",
            "verified_at",
            "freshness_class",
            "fresh_until",
            "authority",
            "status",
            "roles",
            "public_fingerprints",
            "supersedes",
            "superseded_by",
        }
        if set(mapping) != expected:
            raise InvalidTopologyFact("fact_fields_mismatch")
        fresh_until_raw = mapping["fresh_until"]
        return cls(
            schema=mapping["schema"],  # type: ignore[arg-type]
            fact_id=mapping["fact_id"],  # type: ignore[arg-type]
            fact_type=mapping["fact_type"],  # type: ignore[arg-type]
            lookup_key=mapping["lookup_key"],  # type: ignore[arg-type]
            value_class=mapping["value_class"],  # type: ignore[arg-type]
            value_ref=mapping["value_ref"],  # type: ignore[arg-type]
            source=mapping["source"],  # type: ignore[arg-type]
            provenance_ref=mapping["provenance_ref"],  # type: ignore[arg-type]
            verified_revision=mapping["verified_revision"],  # type: ignore[arg-type]
            verified_at=_parse_datetime("verified_at", mapping["verified_at"]),
            freshness_class=mapping["freshness_class"],  # type: ignore[arg-type]
            fresh_until=None if fresh_until_raw is None else _parse_datetime("fresh_until", fresh_until_raw),
            authority=mapping["authority"],  # type: ignore[arg-type]
            status=mapping["status"],  # type: ignore[arg-type]
            roles=_require_string_set("roles", mapping["roles"]),
            public_fingerprints=frozenset(_require_ref("public_fingerprints", item) for item in mapping["public_fingerprints"]),  # type: ignore[union-attr]
            supersedes=_require_string_tuple("supersedes", mapping["supersedes"]),
            superseded_by=mapping["superseded_by"],  # type: ignore[arg-type]
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "fact_id": self.fact_id,
            "fact_type": self.fact_type,
            "lookup_key": self.lookup_key,
            "value_class": self.value_class,
            "value_ref": self.value_ref,
            "source": self.source,
            "provenance_ref": self.provenance_ref,
            "verified_revision": self.verified_revision,
            "verified_at": _format_datetime(self.verified_at),
            "freshness_class": self.freshness_class,
            "fresh_until": None if self.fresh_until is None else _format_datetime(self.fresh_until),
            "authority": self.authority,
            "status": self.status,
            "roles": sorted(self.roles),
            "public_fingerprints": sorted(self.public_fingerprints),
            "supersedes": list(self.supersedes),
            "superseded_by": self.superseded_by,
        }
