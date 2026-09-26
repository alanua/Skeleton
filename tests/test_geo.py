from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from core.geo import (
    Coordinate,
    GeoError,
    GeoProviderUnavailable,
    GeoService,
    InMemoryGeoRepository,
    Place,
    PlaceStatus,
    ProviderIdentity,
    ProviderRouteResult,
    Provenance,
    RouteKind,
    Track,
    TrackPoint,
    TrackRetention,
    TravelMode,
)
from core.geo_mcp import GeoMcpDispatcher, handle_jsonrpc_message
from core.geo_providers import GoogleMapsProviderAdapter, normalize_google_geocode, normalize_google_place, provider_public_config


NOW = "2026-01-01T00:00:00Z"


def place(
    name: str = "Synthetic place",
    *,
    place_id: str | None = None,
    lat: float | None = 52.52,
    lon: float | None = 13.405,
    provider_id: str | None = None,
    category: str = "food",
    address: str | None = "1 Example Street",
) -> Place:
    identity = (ProviderIdentity("fake", provider_id),) if provider_id else ()
    coordinate = Coordinate(lat, lon) if lat is not None and lon is not None else None
    return Place(
        place_id=place_id or f"place_{name.casefold().replace(' ', '_')}",
        canonical_name=name,
        normalized_address=address,
        coordinate=coordinate,
        provider_identities=identity,
        categories=(category,),
        tags=("synthetic",),
        provenance=Provenance("synthetic", source_ref="fixture", retrieved_at=NOW, confidence=1.0),
        saved_at=NOW,
        updated_at=NOW,
    )


class FakeRouteProvider:
    provider = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def optimize_route(self, place_ids: tuple[str, ...], travel_mode: TravelMode) -> ProviderRouteResult:
        if self.fail:
            raise GeoProviderUnavailable("fake")
        return ProviderRouteResult(
            ordered_place_ids=tuple(reversed(place_ids)),
            distance_m=1234.0,
            duration_s=321,
            provider=self.provider,
            provenance=Provenance("fake_routes", retrieved_at=NOW, confidence=1.0),
        )


def test_place_round_trip_and_fixture_are_public_safe() -> None:
    original = place(provider_id="fake-1")
    restored = Place.from_dict(original.to_dict())
    assert restored == original
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/geo/synthetic_places.json").read_text())
    assert fixture["privacy_class"] == "PUBLIC_SAFE_SYNTHETIC"
    assert all(item["privacy_class"] == "PRIVATE_USER_GEO" for item in fixture["places"])


def test_stable_provider_id_dedupes_but_ambiguous_same_name_does_not() -> None:
    repository = InMemoryGeoRepository()
    first = repository.save_place(place(provider_id="stable-1"), idempotency_key="one")
    second = repository.save_place(place("Renamed provider place", provider_id="stable-1"), idempotency_key="two")
    assert first.place.place_id == second.place.place_id
    assert second.idempotency_classification == "DUPLICATE_IDENTITY"

    same_name_a = place("Same name", place_id="same-a", lat=None, lon=None, address=None)
    same_name_b = place("Same name", place_id="same-b", lat=None, lon=None, address=None)
    repository.save_place(same_name_a)
    repository.save_place(same_name_b)
    assert {item.place_id for item in repository.list_places(query="same name")} == {"same-a", "same-b"}


def test_category_tag_bbox_and_nearby_queries() -> None:
    repository = InMemoryGeoRepository()
    repository.save_place(place("Food one", place_id="food-1", lat=52.5200, lon=13.4050))
    repository.save_place(place("Travel one", place_id="travel-1", lat=52.5300, lon=13.4150, category="travel"))
    assert [item.place_id for item in repository.list_places(category="food", tag="synthetic")] == ["food-1"]
    assert {item.place_id for item in repository.query_bbox(min_lat=52.519, min_lon=13.404, max_lat=52.521, max_lon=13.406)} == {"food-1"}
    nearby = repository.nearby(center=Coordinate(52.5200, 13.4050), radius_m=200)
    assert nearby and nearby[0][0].place_id == "food-1"


def test_invalid_and_partial_coordinates_fail_closed() -> None:
    with pytest.raises(GeoError) as invalid:
        Coordinate(91, 13)
    assert invalid.value.reason_code == "COORDINATES_OUT_OF_RANGE"
    with pytest.raises(GeoError) as partial:
        normalize_google_place({"id": "places/x", "displayName": {"text": "Broken"}, "location": {"latitude": 52.5}})
    assert partial.value.reason_code == "PROVIDER_LOCATION_INVALID"


def test_geocoding_confidence_and_provenance_are_preserved() -> None:
    result = normalize_google_geocode({"results": [{"formattedAddress": "Example", "location": {"latitude": 52.5, "longitude": 13.4}, "partialMatch": True, "placeId": "google-x"}]}, retrieved_at=NOW)
    assert result.provenance.confidence == 0.65
    assert result.provenance.source_kind == "google_geocoding"
    assert result.provider_place_id == "google-x"


def test_planned_route_is_not_an_actual_track_and_stop_order_is_explicit() -> None:
    repository = InMemoryGeoRepository()
    for item in (
        place("A", place_id="a", lat=52.5200, lon=13.4050, address="1 A Street"),
        place("B", place_id="b", lat=52.5210, lon=13.4060, address="2 B Street"),
        place("C", place_id="c", lat=52.5220, lon=13.4070, address="3 C Street"),
    ):
        repository.save_place(item)
    service = GeoService(repository, route_provider=FakeRouteProvider())
    planned = service.plan_route(("a", "b", "c"), travel_mode=TravelMode.WALKING)
    optimized = service.optimize_route(("a", "b", "c"), travel_mode=TravelMode.WALKING)
    assert planned.route_kind == RouteKind.PLANNED
    assert optimized.route_kind == RouteKind.PROVIDER_CALCULATED
    assert [stop.place_id for stop in planned.stops] == ["a", "b", "c"]
    assert [stop.sequence for stop in optimized.stops] == [0, 1, 2]
    track = Track("track-1", (TrackPoint(NOW, Coordinate(52.52, 13.405)),), "synthetic-device", TrackRetention.RAW_SHORT, created_at=NOW)
    assert track.track_id != planned.route_id


def test_provider_outage_does_not_create_or_corrupt_canonical_route() -> None:
    repository = InMemoryGeoRepository()
    repository.save_place(place(place_id="a"))
    service = GeoService(repository, route_provider=FakeRouteProvider(fail=True))
    with pytest.raises(GeoProviderUnavailable):
        service.optimize_route(("a",), travel_mode=TravelMode.DRIVING)
    with pytest.raises(GeoError):
        repository.get_route("route_missing")


def test_track_retention_and_simplification_keep_raw_private() -> None:
    repository = InMemoryGeoRepository(retention_seconds={TrackRetention.RAW_SHORT: 10, TrackRetention.DERIVED_LONG: 1000, TrackRetention.EPHEMERAL: 1})
    points = tuple(TrackPoint(f"2026-01-01T00:00:0{i}Z", Coordinate(52.52 + i * 0.00001, 13.405)) for i in range(3))
    raw = Track("raw-1", points, "device-private", TrackRetention.RAW_SHORT, created_at=NOW)
    repository.ingest_track(raw, idempotency_key="track-idem")
    derived = GeoService(repository).simplify_track("raw-1", tolerance_m=2)
    assert len(derived.points) <= len(raw.points)
    assert derived.privacy_class == "PRIVATE_USER_GEO"
    assert derived.track_id != raw.track_id
    assert repository.get_track(raw.track_id).points == raw.points
    assert GeoService(repository).simplify_track("raw-1", tolerance_m=2) == derived
    assert repository.purge_expired_tracks(now="2026-01-01T00:00:11Z") == ("raw-1",)


def test_visit_candidate_requires_confirmation_before_visited() -> None:
    repository = InMemoryGeoRepository()
    repository.save_place(place(place_id="p", lat=52.52, lon=13.405))
    track = Track("t", (TrackPoint(NOW, Coordinate(52.5201, 13.4051)),), "device", TrackRetention.RAW_SHORT, created_at=NOW)
    repository.ingest_track(track, idempotency_key="t-idem")
    service = GeoService(repository)
    candidate = service.detect_visit_candidate(track_id="t", place_id="p", radius_m=100)
    assert candidate.status == "candidate"
    assert repository.get_place("p").status == PlaceStatus.WANT_TO_VISIT
    confirmed = service.confirm_visit(candidate.visit_id)
    assert confirmed.status == "confirmed"
    assert repository.get_place("p").status == PlaceStatus.VISITED


def test_visit_candidate_rejects_non_positive_radius() -> None:
    repository = InMemoryGeoRepository()
    with pytest.raises(GeoError) as error:
        GeoService(repository).detect_visit_candidate(track_id="missing", place_id="missing", radius_m=0)
    assert error.value.reason_code == "INVALID_RADIUS"


class FakeGoogleTransport:
    def text_search(self, query: str, *, location_bias: Coordinate | None = None) -> dict[str, object]:
        return {"places": [{"id": "places/fake", "displayName": {"text": "Google Synthetic"}, "formattedAddress": "1 Example", "location": {"latitude": 52.5, "longitude": 13.4}, "types": ["restaurant"]}]}

    def place_details(self, provider_place_id: str) -> dict[str, object]:
        return {"id": provider_place_id, "displayName": {"text": "Google Details"}, "location": {"latitude": 52.5, "longitude": 13.4}, "types": ["restaurant"]}

    def geocode(self, address: str) -> dict[str, object]:
        return {"results": [{"formattedAddress": address, "location": {"latitude": 52.5, "longitude": 13.4}}]}

    def reverse_geocode(self, coordinate: Coordinate) -> dict[str, object]:
        return {"results": [{"formattedAddress": "1 Example", "location": {"latitude": coordinate.lat, "longitude": coordinate.lon}}]}

    def compute_route(self, place_ids: tuple[str, ...], *, travel_mode: TravelMode) -> dict[str, object]:
        return {"routes": [{"orderedPlaceIds": list(reversed(place_ids)), "distanceMeters": 1000, "durationSeconds": 100}]}


def test_fake_google_adapter_normalizes_and_never_exposes_credentials() -> None:
    adapter = GoogleMapsProviderAdapter(transport=FakeGoogleTransport(), credential_ref="bitwarden-ref")
    result = adapter.search("synthetic")
    assert result[0].provider_identities[0].provider_place_id == "places/fake"
    assert provider_public_config(adapter)["api_key_present"] is False
    assert "bitwarden-ref" not in json.dumps(provider_public_config(adapter))


def test_mcp_schema_is_strict_and_save_place_compatible() -> None:
    dispatcher = GeoMcpDispatcher.synthetic(allow_private_read=False)
    tools = dispatcher.list_tools()
    assert len(tools) >= 20
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)
    arguments = {"place": place(provider_id="mcp-1").to_dict(), "idempotency_key": "save-1"}
    first = dispatcher.call_tool("save_place", arguments)
    second = dispatcher.call_tool("geo.place.save", arguments)
    assert first["status"] == "OK"
    assert first["receipt"]["place_id"] == second["receipt"]["place_id"]
    assert second["receipt"]["idempotency_classification"] == "DUPLICATE_IDENTICAL"
    assert "coordinate" not in json.dumps(first)
    malformed = dispatcher.call_tool("geo.place.save", {"place": {"canonical_name": "x"}, "unexpected": True})
    assert malformed["status"] == "BLOCKED"


def test_public_receipts_never_include_raw_track_points_and_external_mutation_is_gated() -> None:
    dispatcher = GeoMcpDispatcher.synthetic(allow_private_read=False)
    track = Track("public-test", (TrackPoint(NOW, Coordinate(52.5, 13.4)),), "device", TrackRetention.RAW_SHORT, created_at=NOW)
    result = dispatcher.call_tool("geo.track.ingest", {"track": track.to_dict(), "idempotency_key": "track-public"})
    assert result["status"] == "OK"
    assert "52.5" not in json.dumps(result)
    blocked = dispatcher.call_tool("geo.provider.saved_list_mutate", {"action": "add"})
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason_code"] == "EXTERNAL_MUTATION_REQUIRES_GATE"


def test_public_navigation_projection_does_not_emit_private_destination_url() -> None:
    dispatcher = GeoMcpDispatcher.synthetic(allow_private_read=False)
    dispatcher.call_tool("geo.place.save", {"place": place(provider_id="nav-1").to_dict(), "idempotency_key": "nav-save"})
    result = dispatcher.call_tool("geo.open.external_navigation", {"place_id": "place_synthetic_place", "provider": "google_maps"})
    assert result["status"] == "OK"
    assert "navigation_url" not in json.dumps(result)
    private_dispatcher = GeoMcpDispatcher(dispatcher.service, allow_private_read=True)
    private_result = private_dispatcher.call_tool("geo.open.external_navigation", {"place_id": "place_synthetic_place", "provider": "google_maps"})
    assert "navigation_url" in private_result["payload"]


def test_jsonrpc_lifecycle_uses_typed_dispatch() -> None:
    dispatcher = GeoMcpDispatcher.synthetic()
    initialize = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, dispatcher=dispatcher)
    listing = handle_jsonrpc_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, dispatcher=dispatcher)
    assert initialize["result"]["serverInfo"]["name"] == "skeleton-geo"
    assert listing["result"]["tools"]
