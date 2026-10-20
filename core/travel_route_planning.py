from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Final


TRAVEL_ROUTE_PLAN_SCHEMA: Final = "skeleton.travel_route_plan.v1"
ROUTE_MODES: Final = frozenset(
    {"WALK", "BIKE", "DRIVE", "TRANSIT", "RAIL", "FLIGHT", "FERRY"}
)
SOURCE_KINDS: Final = frozenset(
    {"SYNTHETIC", "USER_SUPPLIED_PUBLIC", "CACHED_PUBLIC"}
)
MAX_REQUESTED_MODES: Final = 7
MAX_ALTERNATIVES: Final = 3
MAX_LEGS_PER_ALTERNATIVE: Final = 8
MAX_DURATION_SECONDS: Final = 7 * 86400
MAX_FRESHNESS_SECONDS: Final = 7 * 86400

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_FORBIDDEN_TOKEN_PARTS = (
    "address",
    "addr:",
    "street",
    "latitude",
    "longitude",
    "coordinate",
    "private",
    "google",
    "apple",
    "uber",
    "lyft",
    "booking",
    "payment",
    "credential",
    "oauth",
)
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "route_plan_id",
        "origin_ref",
        "destination_ref",
        "requested_modes",
        "source_metadata",
        "alternatives",
    }
)
_SOURCE_METADATA_KEYS = frozenset(
    {"source_kind", "source_revision", "generated_at", "freshness_seconds"}
)
_ALTERNATIVE_KEYS = frozenset(
    {"alternative_ref", "total_duration_seconds", "modes", "legs"}
)
_LEG_KEYS = frozenset(
    {"leg_ref", "leg_index", "mode", "duration_seconds", "source_ref"}
)


class TravelRoutePlanningValidationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class RouteSourceMetadata:
    source_kind: str
    source_revision: int
    generated_at: int
    freshness_seconds: int

    def __post_init__(self) -> None:
        _enum(self.source_kind, SOURCE_KINDS, "source_kind")
        _positive_int(self.source_revision, "source_revision")
        _non_negative_int(self.generated_at, "generated_at")
        _bounded_int(
            self.freshness_seconds,
            "freshness_seconds",
            minimum=0,
            maximum=MAX_FRESHNESS_SECONDS,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RouteSourceMetadata":
        _exact_keys(value, _SOURCE_METADATA_KEYS, "source_metadata")
        return cls(
            source_kind=value["source_kind"],
            source_revision=value["source_revision"],
            generated_at=value["generated_at"],
            freshness_seconds=value["freshness_seconds"],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_revision": self.source_revision,
            "generated_at": self.generated_at,
            "freshness_seconds": self.freshness_seconds,
        }


@dataclass(frozen=True)
class RouteLeg:
    leg_ref: str
    leg_index: int
    mode: str
    duration_seconds: int
    source_ref: str

    def __post_init__(self) -> None:
        _safe_token(self.leg_ref, "leg_ref")
        _non_negative_int(self.leg_index, "leg_index")
        _enum(self.mode, ROUTE_MODES, "mode")
        _bounded_int(
            self.duration_seconds,
            "duration_seconds",
            minimum=1,
            maximum=MAX_DURATION_SECONDS,
        )
        _safe_token(self.source_ref, "source_ref")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RouteLeg":
        _exact_keys(value, _LEG_KEYS, "leg")
        return cls(
            leg_ref=value["leg_ref"],
            leg_index=value["leg_index"],
            mode=value["mode"],
            duration_seconds=value["duration_seconds"],
            source_ref=value["source_ref"],
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "leg_ref": self.leg_ref,
            "leg_index": self.leg_index,
            "mode": self.mode,
            "duration_seconds": self.duration_seconds,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True)
class RouteAlternative:
    alternative_ref: str
    total_duration_seconds: int
    modes: tuple[str, ...]
    legs: tuple[RouteLeg, ...]

    def __post_init__(self) -> None:
        _safe_token(self.alternative_ref, "alternative_ref")
        _bounded_int(
            self.total_duration_seconds,
            "total_duration_seconds",
            minimum=1,
            maximum=MAX_DURATION_SECONDS,
        )
        modes = _normalized_modes(self.modes, "modes")
        legs = _normalized_legs(self.legs)
        if not legs:
            raise TravelRoutePlanningValidationError(
                "EMPTY_ROUTE_LEGS", "at least one route leg is required"
            )
        leg_modes = tuple(sorted({leg.mode for leg in legs}))
        if modes != leg_modes:
            raise TravelRoutePlanningValidationError(
                "ROUTE_MODE_MISMATCH", "alternative modes must match leg modes"
            )
        duration = sum(leg.duration_seconds for leg in legs)
        if duration != self.total_duration_seconds:
            raise TravelRoutePlanningValidationError(
                "ROUTE_DURATION_MISMATCH",
                "alternative duration must equal summed leg duration",
            )
        object.__setattr__(self, "modes", modes)
        object.__setattr__(self, "legs", legs)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RouteAlternative":
        _exact_keys(value, _ALTERNATIVE_KEYS, "alternative")
        return cls(
            alternative_ref=value["alternative_ref"],
            total_duration_seconds=value["total_duration_seconds"],
            modes=tuple(value["modes"]),
            legs=tuple(RouteLeg.from_mapping(item) for item in value["legs"]),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "alternative_ref": self.alternative_ref,
            "total_duration_seconds": self.total_duration_seconds,
            "modes": list(self.modes),
            "legs": [leg.to_mapping() for leg in self.legs],
        }


@dataclass(frozen=True)
class TravelRoutePlan:
    route_plan_id: str
    origin_ref: str
    destination_ref: str
    requested_modes: tuple[str, ...]
    source_metadata: RouteSourceMetadata
    alternatives: tuple[RouteAlternative, ...]

    def __post_init__(self) -> None:
        _safe_token(self.origin_ref, "origin_ref")
        _safe_token(self.destination_ref, "destination_ref")
        if self.origin_ref == self.destination_ref:
            raise TravelRoutePlanningValidationError(
                "SAME_ROUTE_ENDPOINTS",
                "origin_ref and destination_ref must be distinct opaque refs",
            )
        modes = _normalized_modes(self.requested_modes, "requested_modes")
        alternatives = _normalized_alternatives(self.alternatives)
        if not alternatives:
            raise TravelRoutePlanningValidationError(
                "EMPTY_ROUTE_ALTERNATIVES",
                "at least one route alternative is required",
            )
        if any(not set(item.modes).issubset(modes) for item in alternatives):
            raise TravelRoutePlanningValidationError(
                "UNREQUESTED_ROUTE_MODE",
                "alternative modes must be a subset of requested modes",
            )
        object.__setattr__(self, "requested_modes", modes)
        object.__setattr__(self, "alternatives", alternatives)
        expected = stable_route_plan_id(
            self.origin_ref,
            self.destination_ref,
            self.requested_modes,
            self.source_metadata,
        )
        if self.route_plan_id != expected:
            raise TravelRoutePlanningValidationError(
                "ROUTE_PLAN_ID_MISMATCH", "route_plan_id is not deterministic"
            )

    @classmethod
    def new(
        cls,
        *,
        origin_ref: str,
        destination_ref: str,
        requested_modes: Sequence[str],
        source_metadata: RouteSourceMetadata,
        alternatives: Sequence[RouteAlternative],
    ) -> "TravelRoutePlan":
        modes = _normalized_modes(requested_modes, "requested_modes")
        route_plan_id = stable_route_plan_id(
            origin_ref, destination_ref, modes, source_metadata
        )
        return cls(
            route_plan_id=route_plan_id,
            origin_ref=origin_ref,
            destination_ref=destination_ref,
            requested_modes=modes,
            source_metadata=source_metadata,
            alternatives=tuple(alternatives),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TravelRoutePlan":
        _exact_keys(value, _TOP_LEVEL_KEYS, "travel_route_plan")
        if value["schema"] != TRAVEL_ROUTE_PLAN_SCHEMA:
            raise TravelRoutePlanningValidationError(
                "INVALID_SCHEMA", "travel route plan schema is invalid"
            )
        return cls(
            route_plan_id=value["route_plan_id"],
            origin_ref=value["origin_ref"],
            destination_ref=value["destination_ref"],
            requested_modes=tuple(value["requested_modes"]),
            source_metadata=RouteSourceMetadata.from_mapping(
                value["source_metadata"]
            ),
            alternatives=tuple(
                RouteAlternative.from_mapping(item)
                for item in value["alternatives"]
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": TRAVEL_ROUTE_PLAN_SCHEMA,
            "route_plan_id": self.route_plan_id,
            "origin_ref": self.origin_ref,
            "destination_ref": self.destination_ref,
            "requested_modes": list(self.requested_modes),
            "source_metadata": self.source_metadata.to_mapping(),
            "alternatives": [item.to_mapping() for item in self.alternatives],
        }


def normalize_route_plan(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(TravelRoutePlan.from_mapping(value).to_mapping())


def stable_route_plan_id(
    origin_ref: str,
    destination_ref: str,
    requested_modes: Sequence[str],
    source_metadata: RouteSourceMetadata,
) -> str:
    _safe_token(origin_ref, "origin_ref")
    _safe_token(destination_ref, "destination_ref")
    modes = _normalized_modes(requested_modes, "requested_modes")
    payload = {
        "origin_ref": origin_ref,
        "destination_ref": destination_ref,
        "requested_modes": list(modes),
        "source_metadata": source_metadata.to_mapping(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"travel-route-plan:{digest[:32]}"


def _normalized_modes(value: Sequence[str], field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TravelRoutePlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} must be a bounded sequence"
        )
    if not value or len(value) > MAX_REQUESTED_MODES:
        raise TravelRoutePlanningValidationError(
            f"INVALID_{field.upper()}_COUNT", f"{field} count is out of range"
        )
    modes = tuple(sorted(set(value)))
    if len(modes) != len(value):
        raise TravelRoutePlanningValidationError(
            f"DUPLICATE_{field.upper()}", f"{field} must be unique"
        )
    for mode in modes:
        _enum(mode, ROUTE_MODES, field)
    return modes


def _normalized_legs(value: Sequence[RouteLeg]) -> tuple[RouteLeg, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TravelRoutePlanningValidationError(
            "INVALID_LEGS", "legs must be a bounded sequence"
        )
    if len(value) > MAX_LEGS_PER_ALTERNATIVE:
        raise TravelRoutePlanningValidationError(
            "TOO_MANY_ROUTE_LEGS", "too many route legs were supplied"
        )
    legs = tuple(sorted(value, key=lambda item: item.leg_index))
    if len({item.leg_ref for item in legs}) != len(legs):
        raise TravelRoutePlanningValidationError(
            "DUPLICATE_LEG_REF", "route leg refs must be unique"
        )
    indexes = tuple(item.leg_index for item in legs)
    if indexes != tuple(range(len(legs))):
        raise TravelRoutePlanningValidationError(
            "INVALID_LEG_INDEX", "leg indexes must be contiguous from zero"
        )
    return legs


def _normalized_alternatives(
    value: Sequence[RouteAlternative],
) -> tuple[RouteAlternative, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TravelRoutePlanningValidationError(
            "INVALID_ALTERNATIVES", "alternatives must be a bounded sequence"
        )
    if len(value) > MAX_ALTERNATIVES:
        raise TravelRoutePlanningValidationError(
            "TOO_MANY_ROUTE_ALTERNATIVES",
            "too many route alternatives were supplied",
        )
    alternatives = tuple(sorted(value, key=lambda item: item.alternative_ref))
    if len({item.alternative_ref for item in alternatives}) != len(alternatives):
        raise TravelRoutePlanningValidationError(
            "DUPLICATE_ALTERNATIVE_REF", "route alternative refs must be unique"
        )
    return alternatives


def _exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    record_name: str,
) -> None:
    if not isinstance(value, Mapping):
        raise TravelRoutePlanningValidationError(
            f"INVALID_{record_name.upper()}", f"{record_name} must be a mapping"
        )
    extra = set(value) - expected
    missing = expected - set(value)
    if extra or missing:
        raise TravelRoutePlanningValidationError(
            f"INVALID_{record_name.upper()}_FIELDS",
            f"{record_name} fields are not allowlisted",
        )


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise TravelRoutePlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} must be a bounded opaque token"
        )
    folded = value.casefold()
    if any(part in folded for part in _FORBIDDEN_TOKEN_PARTS):
        raise TravelRoutePlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} must not expose private data"
        )
    return value


def _enum(value: object, allowed: frozenset[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise TravelRoutePlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} is not allowlisted"
        )
    return value


def _non_negative_int(value: object, field: str) -> int:
    return _bounded_int(value, field, minimum=0, maximum=None)


def _positive_int(value: object, field: str) -> int:
    return _bounded_int(value, field, minimum=1, maximum=None)


def _bounded_int(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int | None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TravelRoutePlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} is out of range"
        )
    if maximum is not None and value > maximum:
        raise TravelRoutePlanningValidationError(
            f"INVALID_{field.upper()}", f"{field} is out of range"
        )
    return value
