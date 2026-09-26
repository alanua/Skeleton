"""Provider-neutral Geo capability primitives and private local state boundary.

This module deliberately contains no provider SDK, network client, database path, or
user data.  ``InMemoryGeoRepository`` is a deterministic synthetic/test repository;
production callers must provide the private-state implementation behind the same
service-facing contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import math
import uuid
from typing import Protocol


GEO_CONTRACT_VERSION = "1.0.0"
PRIVATE_GEO = "PRIVATE_USER_GEO"
PUBLIC_SYNTHETIC = "PUBLIC_SAFE_SYNTHETIC"


class GeoError(ValueError):
    """Base error with a stable, public-safe reason code."""

    def __init__(self, reason_code: str, message: str | None = None) -> None:
        super().__init__(message or reason_code)
        self.reason_code = reason_code


class GeoProviderUnavailable(GeoError):
    def __init__(self, provider: str) -> None:
        super().__init__("PROVIDER_UNAVAILABLE", provider)


class PlaceStatus(StrEnum):
    WANT_TO_VISIT = "want_to_visit"
    VISITED = "visited"
    ARCHIVED = "archived"


class RouteKind(StrEnum):
    PLANNED = "planned"
    PROVIDER_CALCULATED = "provider_calculated"


class RouteStatus(StrEnum):
    DRAFT = "draft"
    SAVED = "saved"
    ARCHIVED = "archived"


class TravelMode(StrEnum):
    DRIVING = "driving"
    WALKING = "walking"
    BICYCLING = "bicycling"
    TRANSIT = "transit"


class TrackRetention(StrEnum):
    RAW_SHORT = "raw_short"
    DERIVED_LONG = "derived_long"
    EPHEMERAL = "ephemeral"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GeoError("INVALID_TIMESTAMP", field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GeoError("INVALID_TIMESTAMP", field_name) from exc
    if parsed.tzinfo is None:
        raise GeoError("TIMESTAMP_MUST_BE_TIMEZONE_AWARE", field_name)
    return value


def _text(value: object, field_name: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GeoError("INVALID_TEXT", field_name)
    return value.strip()


def _norm_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _tuple_text(values: Iterable[object], field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        text = _text(value, field_name)
        assert text is not None
        normalized.append(_norm_text(text))
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True)
class Coordinate:
    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not isinstance(self.lat, (int, float)) or not isinstance(self.lon, (int, float)):
            raise GeoError("INVALID_COORDINATES")
        if not math.isfinite(float(self.lat)) or not math.isfinite(float(self.lon)):
            raise GeoError("INVALID_COORDINATES")
        if not -90.0 <= float(self.lat) <= 90.0 or not -180.0 <= float(self.lon) <= 180.0:
            raise GeoError("COORDINATES_OUT_OF_RANGE")

    def to_dict(self) -> dict[str, float]:
        return {"lat": float(self.lat), "lon": float(self.lon)}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Coordinate":
        if not isinstance(value, Mapping) or set(value) != {"lat", "lon"}:
            raise GeoError("INVALID_COORDINATES")
        return cls(float(value["lat"]), float(value["lon"]))


@dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    provider_place_id: str

    def __post_init__(self) -> None:
        _text(self.provider, "provider")
        _text(self.provider_place_id, "provider_place_id")

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "provider_place_id": self.provider_place_id}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ProviderIdentity":
        if not isinstance(value, Mapping) or set(value) != {"provider", "provider_place_id"}:
            raise GeoError("INVALID_PROVIDER_IDENTITY")
        return cls(str(value["provider"]), str(value["provider_place_id"]))


@dataclass(frozen=True)
class Provenance:
    source_kind: str
    source_ref: str | None = None
    retrieved_at: str = field(default_factory=utc_now)
    confidence: float = 0.0
    evidence_hash: str | None = None

    def __post_init__(self) -> None:
        _text(self.source_kind, "source_kind")
        if self.source_ref is not None:
            _text(self.source_ref, "source_ref")
        _timestamp(self.retrieved_at, field_name="retrieved_at")
        if not isinstance(self.confidence, (int, float)) or not 0.0 <= float(self.confidence) <= 1.0:
            raise GeoError("INVALID_CONFIDENCE")
        if self.evidence_hash is not None and (
            not isinstance(self.evidence_hash, str) or len(self.evidence_hash) != 64
        ):
            raise GeoError("INVALID_EVIDENCE_HASH")

    def to_dict(self) -> dict[str, object]:
        return {
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "retrieved_at": self.retrieved_at,
            "confidence": float(self.confidence),
            "evidence_hash": self.evidence_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Provenance":
        if not isinstance(value, Mapping):
            raise GeoError("INVALID_PROVENANCE")
        return cls(
            source_kind=str(value.get("source_kind", "unknown")),
            source_ref=value.get("source_ref") if isinstance(value.get("source_ref"), str) else None,
            retrieved_at=str(value.get("retrieved_at", utc_now())),
            confidence=float(value.get("confidence", 0.0)),
            evidence_hash=value.get("evidence_hash") if isinstance(value.get("evidence_hash"), str) else None,
        )


def stable_place_id(identities: Sequence[ProviderIdentity] = (), *, address: str | None = None,
                    coordinate: Coordinate | None = None) -> str:
    """Create a deterministic ID only from stable non-secret place identity."""

    if identities:
        seed = "|".join(f"{item.provider}:{item.provider_place_id}" for item in sorted(identities, key=lambda x: (x.provider, x.provider_place_id)))
    elif address:
        seed = f"address:{_norm_text(address)}"
    elif coordinate:
        seed = f"coordinate:{coordinate.lat:.5f}:{coordinate.lon:.5f}"
    else:
        return "place_" + uuid.uuid4().hex
    return "place_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Place:
    place_id: str
    canonical_name: str
    normalized_address: str | None = None
    coordinate: Coordinate | None = None
    provider_identities: tuple[ProviderIdentity, ...] = ()
    categories: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    domain_refs: tuple[str, ...] = ()
    note: str | None = None
    what_to_try: str | None = None
    reason_saved: str | None = None
    provenance: Provenance = field(default_factory=lambda: Provenance("manual", confidence=0.5))
    saved_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    status: PlaceStatus = PlaceStatus.WANT_TO_VISIT
    personal_rating: float | None = None
    privacy_class: str = PRIVATE_GEO

    def __post_init__(self) -> None:
        _text(self.place_id, "place_id")
        _text(self.canonical_name, "canonical_name")
        if self.normalized_address is not None:
            _text(self.normalized_address, "normalized_address")
        for field_name, value in (("saved_at", self.saved_at), ("updated_at", self.updated_at)):
            _timestamp(value, field_name=field_name)
        if not isinstance(self.status, PlaceStatus):
            try:
                object.__setattr__(self, "status", PlaceStatus(str(self.status)))
            except ValueError as exc:
                raise GeoError("INVALID_PLACE_STATUS") from exc
        if self.personal_rating is not None and not 0.0 <= float(self.personal_rating) <= 5.0:
            raise GeoError("INVALID_PERSONAL_RATING")
        if self.privacy_class != PRIVATE_GEO:
            raise GeoError("PLACE_PRIVACY_MUST_BE_PRIVATE")

    def to_dict(self, *, include_private: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "place_id": self.place_id,
            "canonical_name": self.canonical_name,
            "normalized_address": self.normalized_address,
            "coordinate": self.coordinate.to_dict() if self.coordinate else None,
            "provider_identities": [item.to_dict() for item in self.provider_identities],
            "categories": list(self.categories),
            "tags": list(self.tags),
            "domain_refs": list(self.domain_refs),
            "note": self.note,
            "what_to_try": self.what_to_try,
            "reason_saved": self.reason_saved,
            "provenance": self.provenance.to_dict(),
            "saved_at": self.saved_at,
            "updated_at": self.updated_at,
            "status": self.status.value,
            "personal_rating": self.personal_rating,
            "privacy_class": self.privacy_class,
        }
        if include_private:
            return value
        return self.public_dict()

    def public_dict(self) -> dict[str, object]:
        """A receipt-safe projection; it intentionally omits address and coordinates."""

        return {
            "place_id": self.place_id,
            "canonical_name": self.canonical_name,
            "categories": list(self.categories),
            "tags": list(self.tags),
            "status": self.status.value,
            "confidence": float(self.provenance.confidence),
            "privacy_class": self.privacy_class,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Place":
        if not isinstance(value, Mapping):
            raise GeoError("PLACE_OBJECT_REQUIRED")
        coordinate_value = value.get("coordinate")
        coordinate = Coordinate.from_dict(coordinate_value) if isinstance(coordinate_value, Mapping) else None
        identities_value = value.get("provider_identities", ())
        if not isinstance(identities_value, Sequence) or isinstance(identities_value, (str, bytes)):
            raise GeoError("INVALID_PROVIDER_IDENTITIES")
        identities = tuple(ProviderIdentity.from_dict(item) for item in identities_value)
        categories = _tuple_text(value.get("categories", ()), "categories")
        tags = _tuple_text(value.get("tags", ()), "tags")
        domain_refs = _tuple_text(value.get("domain_refs", ()), "domain_refs")
        place_id = value.get("place_id")
        if place_id is None:
            place_id = stable_place_id(identities, address=value.get("normalized_address") if isinstance(value.get("normalized_address"), str) else None, coordinate=coordinate)
        now = utc_now()
        return cls(
            place_id=str(place_id),
            canonical_name=str(value.get("canonical_name", "")),
            normalized_address=value.get("normalized_address") if isinstance(value.get("normalized_address"), str) else None,
            coordinate=coordinate,
            provider_identities=identities,
            categories=categories,
            tags=tags,
            domain_refs=domain_refs,
            note=value.get("note") if isinstance(value.get("note"), str) else None,
            what_to_try=value.get("what_to_try") if isinstance(value.get("what_to_try"), str) else None,
            reason_saved=value.get("reason_saved") if isinstance(value.get("reason_saved"), str) else None,
            provenance=Provenance.from_dict(value.get("provenance", {"source_kind": "manual", "confidence": 0.5})),
            saved_at=str(value.get("saved_at", now)),
            updated_at=str(value.get("updated_at", now)),
            status=PlaceStatus(str(value.get("status", PlaceStatus.WANT_TO_VISIT.value))),
            personal_rating=float(value["personal_rating"]) if value.get("personal_rating") is not None else None,
            privacy_class=str(value.get("privacy_class", PRIVATE_GEO)),
        )


@dataclass(frozen=True)
class RouteStop:
    place_id: str
    sequence: int
    dwell_seconds: int = 0

    def __post_init__(self) -> None:
        _text(self.place_id, "route_stop.place_id")
        if not isinstance(self.sequence, int) or self.sequence < 0:
            raise GeoError("INVALID_ROUTE_SEQUENCE")
        if not isinstance(self.dwell_seconds, int) or self.dwell_seconds < 0:
            raise GeoError("INVALID_DWELL_SECONDS")

    def to_dict(self) -> dict[str, object]:
        return {"place_id": self.place_id, "sequence": self.sequence, "dwell_seconds": self.dwell_seconds}


@dataclass(frozen=True)
class Route:
    route_id: str
    route_kind: RouteKind
    stops: tuple[RouteStop, ...]
    travel_mode: TravelMode
    distance_m: float | None = None
    duration_s: int | None = None
    provider: str | None = None
    provenance: Provenance = field(default_factory=lambda: Provenance("local_planner", confidence=1.0))
    status: RouteStatus = RouteStatus.DRAFT
    associated_ref: str | None = None
    saved_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _text(self.route_id, "route_id")
        if not isinstance(self.route_kind, RouteKind):
            object.__setattr__(self, "route_kind", RouteKind(str(self.route_kind)))
        if not isinstance(self.travel_mode, TravelMode):
            object.__setattr__(self, "travel_mode", TravelMode(str(self.travel_mode)))
        sequences = [stop.sequence for stop in self.stops]
        if sequences != list(range(len(self.stops))):
            raise GeoError("ROUTE_STOPS_MUST_BE_ORDERED")
        if len({stop.place_id for stop in self.stops}) != len(self.stops):
            raise GeoError("ROUTE_STOPS_MUST_BE_UNIQUE")
        if self.distance_m is not None and (not math.isfinite(float(self.distance_m)) or float(self.distance_m) < 0):
            raise GeoError("INVALID_ROUTE_DISTANCE")
        if self.duration_s is not None and (not isinstance(self.duration_s, int) or self.duration_s < 0):
            raise GeoError("INVALID_ROUTE_DURATION")
        _timestamp(self.saved_at, field_name="saved_at")
        _timestamp(self.updated_at, field_name="updated_at")

    def to_dict(self, *, include_private: bool = True) -> dict[str, object]:
        value = {
            "route_id": self.route_id,
            "route_kind": self.route_kind.value,
            "stops": [stop.to_dict() for stop in self.stops],
            "travel_mode": self.travel_mode.value,
            "distance_m": self.distance_m,
            "duration_s": self.duration_s,
            "provider": self.provider,
            "provenance": self.provenance.to_dict(),
            "status": self.status.value,
            "associated_ref": self.associated_ref,
            "saved_at": self.saved_at,
            "updated_at": self.updated_at,
        }
        if include_private:
            return value
        return {
            "route_id": self.route_id,
            "route_kind": self.route_kind.value,
            "stop_count": len(self.stops),
            "travel_mode": self.travel_mode.value,
            "status": self.status.value,
            "provider": self.provider,
            "privacy_class": PRIVATE_GEO,
        }


@dataclass(frozen=True)
class TrackPoint:
    timestamp: str
    coordinate: Coordinate
    accuracy_m: float | None = None
    speed_mps: float | None = None
    heading_deg: float | None = None

    def __post_init__(self) -> None:
        _timestamp(self.timestamp, field_name="track_point.timestamp")
        for field_name, value in (("accuracy_m", self.accuracy_m), ("speed_mps", self.speed_mps)):
            if value is not None and (not math.isfinite(float(value)) or float(value) < 0):
                raise GeoError("INVALID_TRACK_METRIC", field_name)
        if self.heading_deg is not None and not 0.0 <= float(self.heading_deg) < 360.0:
            raise GeoError("INVALID_HEADING")

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "coordinate": self.coordinate.to_dict(),
            "accuracy_m": self.accuracy_m,
            "speed_mps": self.speed_mps,
            "heading_deg": self.heading_deg,
        }


@dataclass(frozen=True)
class Track:
    track_id: str
    points: tuple[TrackPoint, ...]
    source_device_ref: str
    retention: TrackRetention
    created_at: str = field(default_factory=utc_now)
    derived_geometry: tuple[Coordinate, ...] = ()
    privacy_class: str = PRIVATE_GEO

    def __post_init__(self) -> None:
        _text(self.track_id, "track_id")
        _text(self.source_device_ref, "source_device_ref")
        if not self.points:
            raise GeoError("TRACK_POINTS_REQUIRED")
        if not isinstance(self.retention, TrackRetention):
            object.__setattr__(self, "retention", TrackRetention(str(self.retention)))
        _timestamp(self.created_at, field_name="created_at")
        if self.privacy_class != PRIVATE_GEO:
            raise GeoError("TRACK_PRIVACY_MUST_BE_PRIVATE")

    def to_dict(self, *, include_private: bool = True) -> dict[str, object]:
        if not include_private:
            return {
                "track_id": self.track_id,
                "point_count": len(self.points),
                "retention": self.retention.value,
                "privacy_class": self.privacy_class,
            }
        return {
            "track_id": self.track_id,
            "points": [point.to_dict() for point in self.points],
            "source_device_ref": self.source_device_ref,
            "retention": self.retention.value,
            "created_at": self.created_at,
            "derived_geometry": [point.to_dict() for point in self.derived_geometry],
            "privacy_class": self.privacy_class,
        }


@dataclass(frozen=True)
class VisitRecord:
    visit_id: str
    place_id: str
    started_at: str
    ended_at: str | None
    source_track_id: str | None
    confidence: float
    status: str = "candidate"
    provenance: Provenance = field(default_factory=lambda: Provenance("geo.visit", confidence=0.5))

    def __post_init__(self) -> None:
        _text(self.visit_id, "visit_id")
        _text(self.place_id, "place_id")
        _timestamp(self.started_at, field_name="started_at")
        if self.ended_at is not None:
            _timestamp(self.ended_at, field_name="ended_at")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise GeoError("INVALID_CONFIDENCE")
        if self.status not in {"candidate", "confirmed", "rejected"}:
            raise GeoError("INVALID_VISIT_STATUS")


@dataclass(frozen=True)
class SaveResult:
    place: Place
    idempotency_classification: str


@dataclass(frozen=True)
class ProviderRouteResult:
    ordered_place_ids: tuple[str, ...]
    distance_m: float | None
    duration_s: int | None
    provider: str
    provenance: Provenance


class RouteProvider(Protocol):
    provider: str

    def optimize_route(self, place_ids: Sequence[str], travel_mode: TravelMode) -> ProviderRouteResult: ...


class GeoRepository(Protocol):
    def save_place(self, place: Place, *, idempotency_key: str | None = None) -> SaveResult: ...


def _provider_key(place: Place) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((item.provider, item.provider_place_id) for item in place.provider_identities))


def _dedupe_key(place: Place) -> tuple[object, ...] | None:
    provider_key = _provider_key(place)
    if provider_key:
        return ("provider", provider_key)
    if place.normalized_address and place.coordinate:
        return ("address_coordinate", _norm_text(place.normalized_address), round(place.coordinate.lat, 5), round(place.coordinate.lon, 5))
    if place.normalized_address:
        return ("address", _norm_text(place.normalized_address))
    if place.coordinate:
        return ("coordinate", round(place.coordinate.lat, 5), round(place.coordinate.lon, 5))
    return None


class InMemoryGeoRepository:
    """Synthetic repository for tests and contract demos, never canonical production state."""

    def __init__(self, *, retention_seconds: Mapping[TrackRetention, int] | None = None) -> None:
        self._places: dict[str, Place] = {}
        self._place_keys: dict[tuple[object, ...], str] = {}
        self._routes: dict[str, Route] = {}
        self._tracks: dict[str, Track] = {}
        self._visits: dict[str, VisitRecord] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self.retention_seconds = dict(retention_seconds or {
            TrackRetention.RAW_SHORT: 7 * 24 * 60 * 60,
            TrackRetention.DERIVED_LONG: 365 * 24 * 60 * 60,
            TrackRetention.EPHEMERAL: 60 * 60,
        })

    def save_place(self, place: Place, *, idempotency_key: str | None = None) -> SaveResult:
        fingerprint = _fingerprint(place.to_dict())
        if idempotency_key is not None:
            previous = self._idempotency.get(idempotency_key)
            if previous is not None:
                if previous[1] != fingerprint:
                    raise GeoError("IDEMPOTENCY_KEY_REUSE_MISMATCH")
                return SaveResult(self._places[previous[0]], "DUPLICATE_IDENTICAL")
        existing_id = place.place_id if place.place_id in self._places else None
        dedupe_key = _dedupe_key(place)
        if existing_id is None and dedupe_key is not None:
            existing_id = self._place_keys.get(dedupe_key)
        if existing_id is not None:
            previous = self._places[existing_id]
            updated = replace(place, place_id=existing_id, saved_at=previous.saved_at, updated_at=utc_now())
            self._reindex_place(previous, updated)
            self._places[existing_id] = updated
            classification = "DUPLICATE_IDENTITY" if previous != updated else "DUPLICATE_IDENTICAL"
            result = SaveResult(updated, classification)
        else:
            self._reindex_place(None, place)
            self._places[place.place_id] = place
            result = SaveResult(place, "NEW")
        if idempotency_key is not None:
            self._idempotency[idempotency_key] = (result.place.place_id, fingerprint)
        return result

    def update_place(self, place: Place, *, idempotency_key: str | None = None) -> SaveResult:
        if place.place_id not in self._places:
            raise GeoError("PLACE_NOT_FOUND")
        return self.save_place(place, idempotency_key=idempotency_key)

    def _reindex_place(self, previous: Place | None, current: Place) -> None:
        key = _dedupe_key(current)
        if key is not None:
            other = self._place_keys.get(key)
            if other is not None and other != current.place_id:
                raise GeoError("AMBIGUOUS_PLACE_IDENTITY")
        if previous is not None:
            old_key = _dedupe_key(previous)
            if old_key is not None and self._place_keys.get(old_key) == previous.place_id:
                self._place_keys.pop(old_key, None)
        if key is not None:
            self._place_keys[key] = current.place_id

    def get_place(self, place_id: str) -> Place:
        try:
            return self._places[place_id]
        except KeyError as exc:
            raise GeoError("PLACE_NOT_FOUND") from exc

    def list_places(self, *, category: str | None = None, tag: str | None = None,
                    status: PlaceStatus | None = None, query: str | None = None) -> tuple[Place, ...]:
        category_norm = _norm_text(category) if category else None
        tag_norm = _norm_text(tag) if tag else None
        query_norm = _norm_text(query) if query else None
        values = []
        for place in self._places.values():
            if category_norm and category_norm not in place.categories:
                continue
            if tag_norm and tag_norm not in place.tags:
                continue
            if status and place.status != status:
                continue
            if query_norm and query_norm not in _norm_text(place.canonical_name) and query_norm not in _norm_text(place.normalized_address or ""):
                continue
            values.append(place)
        return tuple(sorted(values, key=lambda item: (item.saved_at, item.place_id)))

    def query_bbox(self, *, min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> tuple[Place, ...]:
        if min_lat > max_lat or min_lon > max_lon:
            raise GeoError("INVALID_BOUNDING_BOX")
        return tuple(place for place in self._places.values() if place.coordinate and min_lat <= place.coordinate.lat <= max_lat and min_lon <= place.coordinate.lon <= max_lon)

    def nearby(self, *, center: Coordinate, radius_m: float, category: str | None = None) -> tuple[tuple[Place, float], ...]:
        if radius_m <= 0 or not math.isfinite(float(radius_m)):
            raise GeoError("INVALID_RADIUS")
        matches = []
        for place in self.list_places(category=category):
            if place.coordinate is None:
                continue
            distance = haversine_m(center, place.coordinate)
            if distance <= radius_m:
                matches.append((place, distance))
        return tuple(sorted(matches, key=lambda item: (item[1], item[0].place_id)))

    def save_route(self, route: Route) -> Route:
        saved = replace(route, status=RouteStatus.SAVED, updated_at=utc_now())
        self._routes[saved.route_id] = saved
        return saved

    def get_route(self, route_id: str) -> Route:
        try:
            return self._routes[route_id]
        except KeyError as exc:
            raise GeoError("ROUTE_NOT_FOUND") from exc

    def ingest_track(self, track: Track, *, idempotency_key: str | None = None) -> Track:
        fingerprint = _fingerprint(track.to_dict())
        if idempotency_key is not None:
            previous = self._idempotency.get(idempotency_key)
            if previous is not None:
                if previous[1] != fingerprint:
                    raise GeoError("IDEMPOTENCY_KEY_REUSE_MISMATCH")
                return self._tracks[previous[0]]
            self._idempotency[idempotency_key] = (track.track_id, fingerprint)
        self._tracks[track.track_id] = track
        return track

    def get_track(self, track_id: str) -> Track:
        try:
            return self._tracks[track_id]
        except KeyError as exc:
            raise GeoError("TRACK_NOT_FOUND") from exc

    def list_tracks(self) -> tuple[Track, ...]:
        return tuple(sorted(self._tracks.values(), key=lambda item: (item.created_at, item.track_id)))

    def purge_expired_tracks(self, *, now: str) -> tuple[str, ...]:
        now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
        removed: list[str] = []
        for track_id, track in list(self._tracks.items()):
            created_dt = datetime.fromisoformat(track.created_at.replace("Z", "+00:00"))
            ttl = self.retention_seconds[track.retention]
            if (now_dt - created_dt).total_seconds() > ttl:
                removed.append(track_id)
                self._tracks.pop(track_id, None)
        return tuple(removed)

    def save_visit(self, visit: VisitRecord) -> VisitRecord:
        self._visits[visit.visit_id] = visit
        return visit

    def get_visit(self, visit_id: str) -> VisitRecord:
        try:
            return self._visits[visit_id]
        except KeyError as exc:
            raise GeoError("VISIT_NOT_FOUND") from exc

    def list_visits(self, *, place_id: str | None = None, status: str | None = None) -> tuple[VisitRecord, ...]:
        return tuple(sorted((item for item in self._visits.values() if (place_id is None or item.place_id == place_id) and (status is None or item.status == status)), key=lambda item: (item.started_at, item.visit_id)))


class GeoService:
    def __init__(self, repository: InMemoryGeoRepository, *, route_provider: RouteProvider | None = None) -> None:
        self.repository = repository
        self.route_provider = route_provider

    def save_place(self, place: Place, *, idempotency_key: str | None = None) -> SaveResult:
        return self.repository.save_place(place, idempotency_key=idempotency_key)

    def update_place(self, place: Place, *, idempotency_key: str | None = None) -> SaveResult:
        return self.repository.update_place(place, idempotency_key=idempotency_key)

    def archive_place(self, place_id: str) -> Place:
        place = self.repository.get_place(place_id)
        return self.repository.save_place(replace(place, status=PlaceStatus.ARCHIVED, updated_at=utc_now())).place

    def mark_visited(self, place_id: str, *, visited_at: str, source_track_id: str | None = None, confidence: float = 1.0) -> VisitRecord:
        place = self.repository.get_place(place_id)
        _timestamp(visited_at, field_name="visited_at")
        record = VisitRecord("visit_" + uuid.uuid4().hex, place_id, visited_at, visited_at, source_track_id, confidence, "confirmed", Provenance("operator", confidence=confidence))
        self.repository.save_place(replace(place, status=PlaceStatus.VISITED, updated_at=utc_now()))
        return self.repository.save_visit(record)

    def set_rating(self, place_id: str, rating: float) -> Place:
        place = self.repository.get_place(place_id)
        return self.repository.save_place(replace(place, personal_rating=rating, updated_at=utc_now())).place

    def add_tag(self, place_id: str, tag: str) -> Place:
        place = self.repository.get_place(place_id)
        normalized = _norm_text(str(_text(tag, "tag")))
        return self.repository.save_place(replace(place, tags=tuple(dict.fromkeys((*place.tags, normalized))), updated_at=utc_now())).place

    def plan_route(self, place_ids: Sequence[str], *, travel_mode: TravelMode, associated_ref: str | None = None,
                   provider: str | None = None, route_kind: RouteKind = RouteKind.PLANNED,
                   distance_m: float | None = None, duration_s: int | None = None,
                   provenance: Provenance | None = None) -> Route:
        if not place_ids:
            raise GeoError("ROUTE_STOPS_REQUIRED")
        for place_id in place_ids:
            self.repository.get_place(place_id)
        stops = tuple(RouteStop(place_id, index) for index, place_id in enumerate(place_ids))
        return Route(
            route_id="route_" + uuid.uuid4().hex,
            route_kind=route_kind,
            stops=stops,
            travel_mode=travel_mode,
            distance_m=distance_m,
            duration_s=duration_s,
            provider=provider,
            provenance=provenance or Provenance("local_planner", confidence=1.0),
            associated_ref=associated_ref,
        )

    def optimize_route(self, place_ids: Sequence[str], *, travel_mode: TravelMode, associated_ref: str | None = None) -> Route:
        if self.route_provider is None:
            raise GeoProviderUnavailable("route")
        result = self.route_provider.optimize_route(place_ids, travel_mode)
        if set(result.ordered_place_ids) != set(place_ids) or len(result.ordered_place_ids) != len(place_ids):
            raise GeoError("PROVIDER_ROUTE_STOP_SET_MISMATCH")
        return self.plan_route(result.ordered_place_ids, travel_mode=travel_mode, associated_ref=associated_ref,
                               provider=result.provider, route_kind=RouteKind.PROVIDER_CALCULATED,
                               distance_m=result.distance_m, duration_s=result.duration_s, provenance=result.provenance)

    def save_route(self, route: Route) -> Route:
        return self.repository.save_route(route)

    def ingest_track(self, track: Track, *, idempotency_key: str | None = None) -> Track:
        return self.repository.ingest_track(track, idempotency_key=idempotency_key)

    def simplify_track(self, track_id: str, *, tolerance_m: float = 25.0) -> Track:
        if tolerance_m <= 0:
            raise GeoError("INVALID_SIMPLIFICATION_TOLERANCE")
        track = self.repository.get_track(track_id)
        kept: list[TrackPoint] = [track.points[0]]
        for point in track.points[1:-1]:
            if haversine_m(kept[-1].coordinate, point.coordinate) >= tolerance_m:
                kept.append(point)
        if len(track.points) > 1:
            kept.append(track.points[-1])
        simplified = replace(track, points=tuple(kept), derived_geometry=tuple(point.coordinate for point in kept))
        self.repository.ingest_track(simplified)
        return simplified

    def detect_visit_candidate(self, *, track_id: str, place_id: str, radius_m: float = 100.0) -> VisitRecord:
        track = self.repository.get_track(track_id)
        place = self.repository.get_place(place_id)
        if place.coordinate is None:
            raise GeoError("PLACE_COORDINATES_REQUIRED")
        nearest = min(haversine_m(point.coordinate, place.coordinate) for point in track.points)
        if nearest > radius_m:
            raise GeoError("NO_VISIT_CANDIDATE")
        confidence = max(0.0, min(1.0, 1.0 - nearest / radius_m))
        visit = VisitRecord("visit_" + uuid.uuid4().hex, place_id, track.points[0].timestamp, track.points[-1].timestamp, track_id, confidence, "candidate", Provenance("track_correlation", source_ref=track_id, confidence=confidence))
        return self.repository.save_visit(visit)

    def confirm_visit(self, visit_id: str) -> VisitRecord:
        candidate = self.repository.get_visit(visit_id)
        if candidate.status != "candidate":
            return candidate
        confirmed = replace(candidate, status="confirmed")
        self.repository.save_visit(confirmed)
        place = self.repository.get_place(candidate.place_id)
        self.repository.save_place(replace(place, status=PlaceStatus.VISITED, updated_at=utc_now()))
        return confirmed


def haversine_m(first: Coordinate, second: Coordinate) -> float:
    radius = 6_371_000.0
    lat1, lat2 = math.radians(first.lat), math.radians(second.lat)
    dlat = lat2 - lat1
    dlon = math.radians(second.lon - first.lon)
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))


def _fingerprint(value: object) -> str:
    import json

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
