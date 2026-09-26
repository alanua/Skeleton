"""Provider adapters for the provider-neutral Geo contract.

The Google adapter is intentionally transport-injected.  It knows the current
Places/Geocoding/Routes response shapes and endpoint roles, but it never reads a
secret, opens a network connection, or places a key in an Android artifact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from core.geo import (
    Coordinate,
    GeoError,
    GeoProviderUnavailable,
    Place,
    ProviderIdentity,
    ProviderRouteResult,
    Provenance,
    TravelMode,
    stable_place_id,
    utc_now,
)


class ProviderNormalizationError(GeoError):
    def __init__(self, reason_code: str = "PROVIDER_RESPONSE_INVALID") -> None:
        super().__init__(reason_code)


@dataclass(frozen=True)
class GeocodeResult:
    coordinate: Coordinate
    formatted_address: str | None
    provider_place_id: str | None
    provenance: Provenance

    def to_dict(self, *, include_private: bool = True) -> dict[str, object]:
        value = {
            "coordinate": self.coordinate.to_dict(),
            "formatted_address": self.formatted_address,
            "provider_place_id": self.provider_place_id,
            "provenance": self.provenance.to_dict(),
        }
        if include_private:
            return value
        return {
            "provider": "google_maps",
            "confidence": self.provenance.confidence,
            "privacy_class": "PRIVATE_USER_GEO",
        }


class GoogleMapsTransport(Protocol):
    def text_search(self, query: str, *, location_bias: Coordinate | None = None) -> Mapping[str, object]: ...

    def place_details(self, provider_place_id: str) -> Mapping[str, object]: ...

    def geocode(self, address: str) -> Mapping[str, object]: ...

    def reverse_geocode(self, coordinate: Coordinate) -> Mapping[str, object]: ...

    def compute_route(self, place_ids: Sequence[str], *, travel_mode: TravelMode) -> Mapping[str, object]: ...


class GoogleMapsProviderAdapter:
    provider = "google_maps"
    required_secret_ref = "Bitwarden:Skeleton/Google Maps Platform"

    def __init__(self, *, transport: GoogleMapsTransport | None = None, credential_ref: str | None = None) -> None:
        self.transport = transport
        self.credential_ref = credential_ref

    def _transport(self) -> GoogleMapsTransport:
        if self.transport is None or not self.credential_ref:
            raise GeoProviderUnavailable(self.provider)
        return self.transport

    def search(self, query: str, *, location_bias: Coordinate | None = None) -> tuple[Place, ...]:
        raw = self._transport().text_search(query, location_bias=location_bias)
        places = raw.get("places", ())
        if not isinstance(places, Sequence) or isinstance(places, (str, bytes)):
            raise ProviderNormalizationError()
        return tuple(normalize_google_place(item) for item in places if isinstance(item, Mapping))

    def place_details(self, provider_place_id: str) -> Place:
        raw = self._transport().place_details(provider_place_id)
        return normalize_google_place(raw)

    def geocode(self, address: str) -> GeocodeResult:
        return normalize_google_geocode(self._transport().geocode(address))

    def reverse_geocode(self, coordinate: Coordinate) -> GeocodeResult:
        return normalize_google_geocode(self._transport().reverse_geocode(coordinate))

    def optimize_route(self, place_ids: Sequence[str], travel_mode: TravelMode) -> ProviderRouteResult:
        raw = self._transport().compute_route(place_ids, travel_mode=travel_mode)
        routes = raw.get("routes")
        if isinstance(routes, Sequence) and not isinstance(routes, (str, bytes)) and routes:
            first = routes[0]
        else:
            first = raw
        if not isinstance(first, Mapping):
            raise ProviderNormalizationError()
        ordered = first.get("orderedPlaceIds", first.get("optimizedPlaceIds", place_ids))
        if not isinstance(ordered, Sequence) or isinstance(ordered, (str, bytes)):
            raise ProviderNormalizationError("PROVIDER_ROUTE_ORDER_INVALID")
        distance = first.get("distanceMeters")
        duration = first.get("durationSeconds")
        if duration is None and isinstance(first.get("duration"), str) and first["duration"].endswith("s"):
            duration = float(first["duration"][:-1])
        return ProviderRouteResult(
            ordered_place_ids=tuple(str(item) for item in ordered),
            distance_m=float(distance) if distance is not None else None,
            duration_s=int(float(duration)) if duration is not None else None,
            provider=self.provider,
            provenance=Provenance("google_routes", confidence=0.95),
        )


def normalize_google_place(raw: Mapping[str, object], *, retrieved_at: str | None = None) -> Place:
    if not isinstance(raw, Mapping):
        raise ProviderNormalizationError()
    provider_place_id = raw.get("id") or raw.get("placeId")
    if not isinstance(provider_place_id, str) or not provider_place_id.strip():
        raise ProviderNormalizationError("PROVIDER_PLACE_ID_REQUIRED")
    display_name = raw.get("displayName")
    if isinstance(display_name, Mapping):
        name = display_name.get("text")
    else:
        name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProviderNormalizationError("PROVIDER_PLACE_NAME_REQUIRED")
    address = raw.get("formattedAddress") or raw.get("formatted_address")
    if address is not None and not isinstance(address, str):
        raise ProviderNormalizationError("PROVIDER_ADDRESS_INVALID")
    location = raw.get("location")
    coordinate = None
    if location is not None:
        if not isinstance(location, Mapping) or "latitude" not in location or "longitude" not in location:
            raise ProviderNormalizationError("PROVIDER_LOCATION_INVALID")
        try:
            coordinate = Coordinate(float(location["latitude"]), float(location["longitude"]))
        except (TypeError, ValueError, GeoError) as exc:
            raise ProviderNormalizationError("PROVIDER_LOCATION_INVALID") from exc
    types = raw.get("types", ())
    if not isinstance(types, Sequence) or isinstance(types, (str, bytes)):
        raise ProviderNormalizationError("PROVIDER_TYPES_INVALID")
    identity = ProviderIdentity("google_maps", provider_place_id)
    source_ref = raw.get("googleMapsUri") if isinstance(raw.get("googleMapsUri"), str) else None
    confidence = 0.95 if coordinate is not None else 0.75
    provenance = Provenance("google_places", source_ref=source_ref, retrieved_at=retrieved_at or utc_now(), confidence=confidence)
    return Place(
        place_id=stable_place_id((identity,)),
        canonical_name=name.strip(),
        normalized_address=address.strip() if isinstance(address, str) else None,
        coordinate=coordinate,
        provider_identities=(identity,),
        categories=tuple(str(item).casefold() for item in types if isinstance(item, str)),
        provenance=provenance,
    )


def normalize_google_geocode(raw: Mapping[str, object], *, retrieved_at: str | None = None) -> GeocodeResult:
    if not isinstance(raw, Mapping):
        raise ProviderNormalizationError()
    candidates = raw.get("results")
    if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)):
        if not candidates:
            raise ProviderNormalizationError("GEOCODE_NO_RESULTS")
        first = candidates[0]
    else:
        first = raw
    if not isinstance(first, Mapping):
        raise ProviderNormalizationError("GEOCODE_RESULT_INVALID")
    location = first.get("location")
    if location is None and isinstance(first.get("geometry"), Mapping):
        geometry = first["geometry"]
        location = geometry.get("location") if isinstance(geometry, Mapping) else None
    if not isinstance(location, Mapping) or "latitude" not in location or "longitude" not in location:
        raise ProviderNormalizationError("GEOCODE_LOCATION_REQUIRED")
    try:
        coordinate = Coordinate(float(location["latitude"]), float(location["longitude"]))
    except (TypeError, ValueError, GeoError) as exc:
        raise ProviderNormalizationError("GEOCODE_LOCATION_INVALID") from exc
    address = first.get("formattedAddress") or first.get("formatted_address")
    if address is not None and not isinstance(address, str):
        raise ProviderNormalizationError("GEOCODE_ADDRESS_INVALID")
    place_id = first.get("placeId") or first.get("place_id")
    if place_id is not None and not isinstance(place_id, str):
        raise ProviderNormalizationError("GEOCODE_PLACE_ID_INVALID")
    partial = bool(first.get("partialMatch", first.get("partial_match", False)))
    confidence = 0.65 if partial else 0.9
    provenance = Provenance("google_geocoding", retrieved_at=retrieved_at or utc_now(), confidence=confidence)
    return GeocodeResult(coordinate, address.strip() if isinstance(address, str) else None, place_id, provenance)


def provider_public_config(adapter: GoogleMapsProviderAdapter) -> dict[str, object]:
    """Return safe diagnostics without emitting the credential or its value."""

    return {
        "provider": adapter.provider,
        "configured": bool(adapter.transport is not None and adapter.credential_ref),
        "credential_ref_present": bool(adapter.credential_ref),
        "required_secret_ref": adapter.required_secret_ref,
        "api_key_present": False,
    }
