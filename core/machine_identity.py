from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Mapping


_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,63}$")
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{2,127}$")
_FINGERPRINT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:+/=_-]{15,255}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,95}$")


class InvalidMachineIdentity(ValueError):
    pass


def _require_identifier(name: str, value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise InvalidMachineIdentity(f"invalid_{name}")
    return value


def _require_token(name: str, value: object) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise InvalidMachineIdentity(f"invalid_{name}")
    return value


def _require_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidMachineIdentity(f"invalid_{name}")
    return value.astimezone(timezone.utc)


def _parse_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise InvalidMachineIdentity(f"invalid_{name}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidMachineIdentity(f"invalid_{name}") from exc
    return _require_datetime(name, parsed)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_tokens(name: str, values: object) -> frozenset[str]:
    if not isinstance(values, (tuple, list, set, frozenset)) or not values:
        raise InvalidMachineIdentity(f"invalid_{name}")
    result = frozenset(_require_token(name, value) for value in values)
    if len(result) != len(values):
        raise InvalidMachineIdentity(f"duplicate_{name}")
    return result


@dataclass(frozen=True)
class MachineIdentity:
    machine_id: str
    node_class: str
    key_id: str
    key_version: int
    public_fingerprint: str
    transport_profiles: frozenset[str]
    capabilities: frozenset[str]
    issued_at: datetime
    expires_at: datetime | None
    last_verified_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "machine_id", _require_identifier("machine_id", self.machine_id))
        object.__setattr__(self, "node_class", _require_identifier("node_class", self.node_class))
        if not isinstance(self.key_id, str) or not _KEY_ID_RE.fullmatch(self.key_id):
            raise InvalidMachineIdentity("invalid_key_id")
        if not isinstance(self.key_version, int) or isinstance(self.key_version, bool) or self.key_version < 1:
            raise InvalidMachineIdentity("invalid_key_version")
        if not isinstance(self.public_fingerprint, str) or not _FINGERPRINT_RE.fullmatch(self.public_fingerprint):
            raise InvalidMachineIdentity("invalid_public_fingerprint")
        object.__setattr__(self, "transport_profiles", _require_tokens("transport_profiles", self.transport_profiles))
        object.__setattr__(self, "capabilities", _require_tokens("capabilities", self.capabilities))
        issued = _require_datetime("issued_at", self.issued_at)
        verified = _require_datetime("last_verified_at", self.last_verified_at)
        expires = None if self.expires_at is None else _require_datetime("expires_at", self.expires_at)
        if verified < issued:
            raise InvalidMachineIdentity("verification_precedes_issue")
        if expires is not None and expires <= issued:
            raise InvalidMachineIdentity("expiry_precedes_issue")
        object.__setattr__(self, "issued_at", issued)
        object.__setattr__(self, "last_verified_at", verified)
        object.__setattr__(self, "expires_at", expires)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "MachineIdentity":
        if not isinstance(mapping, Mapping):
            raise InvalidMachineIdentity("identity_must_be_mapping")
        expected = {
            "machine_id",
            "node_class",
            "key_id",
            "key_version",
            "public_fingerprint",
            "transport_profiles",
            "capabilities",
            "issued_at",
            "expires_at",
            "last_verified_at",
        }
        if set(mapping) != expected:
            raise InvalidMachineIdentity("identity_fields_mismatch")
        expires_raw = mapping["expires_at"]
        return cls(
            machine_id=mapping["machine_id"],  # type: ignore[arg-type]
            node_class=mapping["node_class"],  # type: ignore[arg-type]
            key_id=mapping["key_id"],  # type: ignore[arg-type]
            key_version=mapping["key_version"],  # type: ignore[arg-type]
            public_fingerprint=mapping["public_fingerprint"],  # type: ignore[arg-type]
            transport_profiles=_require_tokens("transport_profiles", mapping["transport_profiles"]),
            capabilities=_require_tokens("capabilities", mapping["capabilities"]),
            issued_at=_parse_datetime("issued_at", mapping["issued_at"]),
            expires_at=None if expires_raw is None else _parse_datetime("expires_at", expires_raw),
            last_verified_at=_parse_datetime("last_verified_at", mapping["last_verified_at"]),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "machine_id": self.machine_id,
            "node_class": self.node_class,
            "key_id": self.key_id,
            "key_version": self.key_version,
            "public_fingerprint": self.public_fingerprint,
            "transport_profiles": sorted(self.transport_profiles),
            "capabilities": sorted(self.capabilities),
            "issued_at": _format_datetime(self.issued_at),
            "expires_at": None if self.expires_at is None else _format_datetime(self.expires_at),
            "last_verified_at": _format_datetime(self.last_verified_at),
        }
