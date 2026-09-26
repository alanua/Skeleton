"""Typed MCP-facing dispatcher for the shared Geo capability.

This is a library surface, not a second server, registry, or auth system.  The
existing Skeleton MCP runtime can register these tool descriptions and delegate
calls to this dispatcher once the protected runtime wiring is approved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from urllib.parse import urlencode

from core.geo import (
    Coordinate,
    GeoError,
    GeoProviderUnavailable,
    GeoService,
    InMemoryGeoRepository,
    Place,
    PlaceStatus,
    Provenance,
    Route,
    RouteKind,
    RouteStatus,
    RouteStop,
    Track,
    TrackPoint,
    TrackRetention,
    TravelMode,
    VisitRecord,
    PRIVATE_GEO,
    ProviderRouteResult,
    utc_now,
)


GEO_MCP_SCHEMA = "skeleton.geo_mcp.v1"
SERVER_NAME = "skeleton-geo"
SERVER_VERSION = "0.1.0"


class GeoMcpValidationError(GeoError):
    def __init__(self, reason_code: str = "INVALID_GEO_REQUEST") -> None:
        super().__init__(reason_code)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, object]
    side_effect_class: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
            "sideEffectClass": self.side_effect_class,
        }


def _object(properties: Mapping[str, object], *, required: Sequence[str] = (), nullable: bool = False) -> dict[str, object]:
    value: dict[str, object] = {
        "type": ["object", "null"] if nullable else "object",
        "additionalProperties": False,
        "required": list(required),
        "properties": dict(properties),
    }
    return value


def _string(*, enum: Sequence[str] | None = None, min_length: int | None = None, nullable: bool = False) -> dict[str, object]:
    result: dict[str, object] = {"type": ["string", "null"] if nullable else "string"}
    if enum is not None:
        result["enum"] = list(enum)
    if min_length is not None:
        result["minLength"] = min_length
    return result


def _number(*, minimum: float | None = None, maximum: float | None = None, nullable: bool = False) -> dict[str, object]:
    result: dict[str, object] = {"type": ["number", "null"] if nullable else "number"}
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    return result


COORDINATE_SCHEMA = _object(
    {"lat": _number(minimum=-90, maximum=90), "lon": _number(minimum=-180, maximum=180)},
    required=("lat", "lon"),
)
PROVIDER_IDENTITY_SCHEMA = _object(
    {"provider": _string(min_length=1), "provider_place_id": _string(min_length=1)},
    required=("provider", "provider_place_id"),
)
PROVENANCE_SCHEMA = _object(
    {
        "source_kind": _string(min_length=1),
        "source_ref": _string(min_length=1, nullable=True),
        "retrieved_at": _string(min_length=1),
        "confidence": _number(minimum=0, maximum=1),
        "evidence_hash": _string(min_length=64, nullable=True),
    }
)
PLACE_SCHEMA = _object(
    {
        "place_id": _string(min_length=1),
        "canonical_name": _string(min_length=1),
        "normalized_address": _string(min_length=1, nullable=True),
        "coordinate": _object(COORDINATE_SCHEMA["properties"], required=("lat", "lon"), nullable=True),
        "provider_identities": {"type": "array", "items": PROVIDER_IDENTITY_SCHEMA, "uniqueItems": True},
        "categories": {"type": "array", "items": _string(min_length=1), "uniqueItems": True},
        "tags": {"type": "array", "items": _string(min_length=1), "uniqueItems": True},
        "domain_refs": {"type": "array", "items": _string(min_length=1), "uniqueItems": True},
        "note": _string(min_length=1, nullable=True),
        "what_to_try": _string(min_length=1, nullable=True),
        "reason_saved": _string(min_length=1, nullable=True),
        "provenance": PROVENANCE_SCHEMA,
        "saved_at": _string(min_length=1),
        "updated_at": _string(min_length=1),
        "status": _string(enum=[item.value for item in PlaceStatus]),
        "personal_rating": _number(minimum=0, maximum=5, nullable=True),
        "privacy_class": _string(enum=[PRIVATE_GEO]),
    },
    required=("canonical_name",),
)
ROUTE_STOP_SCHEMA = _object(
    {"place_id": _string(min_length=1), "sequence": {"type": "integer", "minimum": 0}, "dwell_seconds": {"type": "integer", "minimum": 0}},
    required=("place_id", "sequence"),
)
ROUTE_SCHEMA = _object(
    {
        "route_id": _string(min_length=1),
        "route_kind": _string(enum=[item.value for item in RouteKind]),
        "stops": {"type": "array", "items": ROUTE_STOP_SCHEMA, "minItems": 1},
        "travel_mode": _string(enum=[item.value for item in TravelMode]),
        "distance_m": _number(minimum=0, nullable=True),
        "duration_s": {"type": ["integer", "null"], "minimum": 0},
        "provider": _string(min_length=1, nullable=True),
        "provenance": PROVENANCE_SCHEMA,
        "status": _string(enum=[item.value for item in RouteStatus]),
        "associated_ref": _string(min_length=1, nullable=True),
        "saved_at": _string(min_length=1),
        "updated_at": _string(min_length=1),
    },
    required=("route_id", "route_kind", "stops", "travel_mode"),
)
TRACK_POINT_SCHEMA = _object(
    {
        "timestamp": _string(min_length=1),
        "coordinate": COORDINATE_SCHEMA,
        "accuracy_m": _number(minimum=0, nullable=True),
        "speed_mps": _number(minimum=0, nullable=True),
        "heading_deg": _number(minimum=0, maximum=359.999999, nullable=True),
    },
    required=("timestamp", "coordinate"),
)
TRACK_SCHEMA = _object(
    {
        "track_id": _string(min_length=1),
        "points": {"type": "array", "items": TRACK_POINT_SCHEMA, "minItems": 1},
        "source_device_ref": _string(min_length=1),
        "retention": _string(enum=[item.value for item in TrackRetention]),
        "created_at": _string(min_length=1),
        "derived_geometry": {"type": "array", "items": COORDINATE_SCHEMA},
        "privacy_class": _string(enum=[PRIVATE_GEO]),
    },
    required=("track_id", "points", "source_device_ref", "retention"),
)


def _tool_specs() -> tuple[ToolSpec, ...]:
    place_id = _string(min_length=1)
    idempotency = _string(min_length=1)
    place_save = _object({"place": PLACE_SCHEMA, "idempotency_key": idempotency}, required=("place", "idempotency_key"))
    return (
        ToolSpec("geo.place.search", "Search provider places without mutating canonical state.", _object({"query": _string(min_length=1), "provider": _string(min_length=1), "location_bias": COORDINATE_SCHEMA}), "read_only"),
        ToolSpec("geo.place.resolve", "Resolve one provider place into the normalized Geo model.", _object({"provider": _string(min_length=1), "provider_place_id": _string(min_length=1)}, required=("provider", "provider_place_id")), "read_only"),
        ToolSpec("geo.place.get", "Read one canonical private place by stable ID.", _object({"place_id": place_id}, required=("place_id",)), "read_only"),
        ToolSpec("geo.place.save", "Idempotently save one normalized private place.", place_save, "private_local_mutation"),
        ToolSpec("geo.place.update", "Idempotently update one normalized private place.", place_save, "private_local_mutation"),
        ToolSpec("geo.place.archive", "Archive one canonical private place.", _object({"place_id": place_id}, required=("place_id",)), "private_local_mutation"),
        ToolSpec("geo.place.mark_visited", "Mark a place visited and create a confirmed visit record.", _object({"place_id": place_id, "visited_at": _string(min_length=1), "source_track_id": _string(min_length=1), "confidence": _number(minimum=0, maximum=1)}, required=("place_id", "visited_at")), "private_local_mutation"),
        ToolSpec("geo.place.rate", "Set a private personal rating without provider mutation.", _object({"place_id": place_id, "rating": _number(minimum=0, maximum=5)}, required=("place_id", "rating")), "private_local_mutation"),
        ToolSpec("geo.place.tag", "Add one normalized private tag.", _object({"place_id": place_id, "tag": _string(min_length=1)}, required=("place_id", "tag")), "private_local_mutation"),
        ToolSpec("geo.place.list", "List canonical places with category, tag, status and text filters.", _object({"category": _string(min_length=1), "tag": _string(min_length=1), "status": _string(enum=[item.value for item in PlaceStatus]), "query": _string(min_length=1)}), "read_only"),
        ToolSpec("geo.place.nearby", "Query canonical places in a private radius.", _object({"center": COORDINATE_SCHEMA, "radius_m": _number(minimum=0.1), "category": _string(min_length=1)}, required=("center", "radius_m")), "read_only"),
        ToolSpec("geo.route.plan", "Create an unsaved planned route with ordered stops.", _object({"place_ids": {"type": "array", "items": place_id, "minItems": 1}, "travel_mode": _string(enum=[item.value for item in TravelMode]), "associated_ref": _string(min_length=1)}, required=("place_ids", "travel_mode")), "read_only"),
        ToolSpec("geo.route.compare", "Compare a local ordered plan with a provider calculation.", _object({"place_ids": {"type": "array", "items": place_id, "minItems": 1}, "travel_mode": _string(enum=[item.value for item in TravelMode])}, required=("place_ids", "travel_mode")), "read_only"),
        ToolSpec("geo.route.optimize", "Calculate an optimized provider route without saving it.", _object({"place_ids": {"type": "array", "items": place_id, "minItems": 1}, "travel_mode": _string(enum=[item.value for item in TravelMode]), "associated_ref": _string(min_length=1)}, required=("place_ids", "travel_mode")), "read_only"),
        ToolSpec("geo.route.save", "Save a planned or provider-calculated route as private state.", _object({"route": ROUTE_SCHEMA}, required=("route",)), "private_local_mutation"),
        ToolSpec("geo.route.get", "Read one private route by stable ID.", _object({"route_id": _string(min_length=1)}, required=("route_id",)), "read_only"),
        ToolSpec("geo.track.ingest", "Ingest private timestamped track points under retention policy.", _object({"track": TRACK_SCHEMA, "idempotency_key": idempotency}, required=("track", "idempotency_key")), "private_local_mutation"),
        ToolSpec("geo.track.query", "Read private track data or safe track summaries.", _object({"track_id": _string(min_length=1)},), "read_only"),
        ToolSpec("geo.track.simplify", "Create a simplified derived track while preserving the raw/derived distinction.", _object({"track_id": _string(min_length=1), "tolerance_m": _number(minimum=0.1)}, required=("track_id",)), "private_local_mutation"),
        ToolSpec("geo.visit.detect_candidate", "Correlate a track with a place as an unconfirmed visit candidate.", _object({"track_id": _string(min_length=1), "place_id": place_id, "radius_m": _number(minimum=0.1)}, required=("track_id", "place_id")), "private_local_mutation"),
        ToolSpec("geo.visit.confirm", "Confirm a visit candidate and update place status.", _object({"visit_id": _string(min_length=1)}, required=("visit_id",)), "private_local_mutation"),
        ToolSpec("geo.visit.history", "Read private visit history.", _object({"place_id": place_id, "status": _string(enum=["candidate", "confirmed", "rejected"]) }), "read_only"),
        ToolSpec("geo.provider.geocode", "Use the selected geocoding provider; does not write canonical state.", _object({"provider": _string(min_length=1), "address": _string(min_length=1)}, required=("provider", "address")), "read_only"),
        ToolSpec("geo.provider.reverse_geocode", "Use the selected reverse-geocoding provider; does not write canonical state.", _object({"provider": _string(min_length=1), "coordinate": COORDINATE_SCHEMA}, required=("provider", "coordinate")), "read_only"),
        ToolSpec("geo.provider.route", "Calculate a provider route without saving it.", _object({"provider": _string(min_length=1), "place_ids": {"type": "array", "items": place_id, "minItems": 1}, "travel_mode": _string(enum=[item.value for item in TravelMode])}, required=("provider", "place_ids", "travel_mode")), "read_only"),
        ToolSpec("geo.open.external_navigation", "Build a navigation deep link; it does not mutate a provider account.", _object({"place_id": place_id, "provider": _string(enum=["google_maps"])} , required=("place_id",)), "read_only"),
        ToolSpec("geo.provider.saved_list_mutate", "External Google Saved List mutation is separately gated and disabled in Geo v1.", _object({"action": _string(min_length=1)}, required=("action",)), "external_mutation"),
    )


TOOL_SPECS = _tool_specs()
TOOL_BY_NAME = {item.name: item for item in TOOL_SPECS}


class GeoMcpDispatcher:
    def __init__(self, service: GeoService, *, provider: object | None = None, allow_private_read: bool = False) -> None:
        self.service = service
        self.provider = provider
        if self.service.route_provider is None and provider is not None and hasattr(provider, "optimize_route"):
            self.service.route_provider = provider  # type: ignore[assignment]
        self.allow_private_read = allow_private_read

    @classmethod
    def synthetic(cls, *, allow_private_read: bool = False) -> "GeoMcpDispatcher":
        repository = InMemoryGeoRepository()
        service = GeoService(repository)
        return cls(service, allow_private_read=allow_private_read)

    def list_tools(self) -> tuple[dict[str, object], ...]:
        return tuple(item.as_dict() for item in TOOL_SPECS)

    def call_tool(self, name: str, arguments: Mapping[str, object] | None) -> dict[str, object]:
        canonical_name = "geo.place.save" if name == "save_place" else name
        spec = TOOL_BY_NAME.get(canonical_name)
        if spec is None:
            return _blocked("UNSUPPORTED_TOOL", tool=name)
        try:
            if not isinstance(arguments, Mapping):
                raise GeoMcpValidationError("ARGUMENTS_OBJECT_REQUIRED")
            _validate_schema(spec.input_schema, arguments)
            if spec.side_effect_class == "external_mutation":
                return _blocked("EXTERNAL_MUTATION_REQUIRES_GATE", tool=canonical_name, side_effect_class=spec.side_effect_class)
            payload, receipt = self._dispatch(canonical_name, arguments)
            return _ok(spec, self._project(payload), receipt)
        except GeoError as exc:
            return _blocked(exc.reason_code, tool=canonical_name, side_effect_class=spec.side_effect_class)
        except (KeyError, TypeError, ValueError):
            return _blocked("MALFORMED_REQUEST", tool=canonical_name, side_effect_class=spec.side_effect_class)

    def _dispatch(self, name: str, arguments: Mapping[str, object]) -> tuple[object, dict[str, object]]:
        if name == "geo.place.search":
            provider = self._provider_for(arguments)
            if not hasattr(provider, "search"):
                raise GeoProviderUnavailable(str(arguments.get("provider", "unknown")))
            results = provider.search(str(arguments["query"]), location_bias=_coordinate(arguments.get("location_bias")))
            return {"places": results}, {"result_count": len(results)}
        if name == "geo.place.resolve":
            provider = self._provider_for(arguments)
            if not hasattr(provider, "place_details"):
                raise GeoProviderUnavailable(str(arguments["provider"]))
            place = provider.place_details(str(arguments["provider_place_id"]))
            return {"place": place}, {"place_id": place.place_id, "provider_evidence": True}
        if name == "geo.place.get":
            place = self.service.repository.get_place(str(arguments["place_id"]))
            return {"place": place}, {"place_id": place.place_id}
        if name in {"geo.place.save", "geo.place.update"}:
            place = Place.from_dict(arguments["place"])
            method = self.service.save_place if name == "geo.place.save" else self.service.update_place
            result = method(place, idempotency_key=str(arguments["idempotency_key"]))
            return {"place": result.place}, {"place_id": result.place.place_id, "idempotency_classification": result.idempotency_classification}
        if name == "geo.place.archive":
            place = self.service.archive_place(str(arguments["place_id"]))
            return {"place": place}, {"place_id": place.place_id, "status": place.status.value}
        if name == "geo.place.mark_visited":
            visit = self.service.mark_visited(str(arguments["place_id"]), visited_at=str(arguments["visited_at"]), source_track_id=arguments.get("source_track_id") if isinstance(arguments.get("source_track_id"), str) else None, confidence=float(arguments.get("confidence", 1.0)))
            return {"visit": visit}, {"visit_id": visit.visit_id, "place_id": visit.place_id, "status": visit.status}
        if name == "geo.place.rate":
            place = self.service.set_rating(str(arguments["place_id"]), float(arguments["rating"]))
            return {"place": place}, {"place_id": place.place_id}
        if name == "geo.place.tag":
            place = self.service.add_tag(str(arguments["place_id"]), str(arguments["tag"]))
            return {"place": place}, {"place_id": place.place_id, "tag_added": str(arguments["tag"])}
        if name == "geo.place.list":
            values = self.service.repository.list_places(category=_optional_str(arguments, "category"), tag=_optional_str(arguments, "tag"), status=PlaceStatus(str(arguments["status"])) if arguments.get("status") else None, query=_optional_str(arguments, "query"))
            return {"places": values}, {"result_count": len(values)}
        if name == "geo.place.nearby":
            matches = self.service.repository.nearby(center=_coordinate(arguments["center"]), radius_m=float(arguments["radius_m"]), category=_optional_str(arguments, "category"))
            return {"places": tuple({"place": place, "distance_m": distance} for place, distance in matches)}, {"result_count": len(matches)}
        if name == "geo.route.plan":
            route = self.service.plan_route(tuple(str(item) for item in arguments["place_ids"]), travel_mode=TravelMode(str(arguments["travel_mode"])), associated_ref=_optional_str(arguments, "associated_ref"))
            return {"route": route}, {"route_id": route.route_id, "route_kind": route.route_kind.value}
        if name == "geo.route.optimize":
            route = self.service.optimize_route(tuple(str(item) for item in arguments["place_ids"]), travel_mode=TravelMode(str(arguments["travel_mode"])), associated_ref=_optional_str(arguments, "associated_ref"))
            return {"route": route}, {"route_id": route.route_id, "route_kind": route.route_kind.value}
        if name == "geo.route.compare":
            planned = self.service.plan_route(tuple(str(item) for item in arguments["place_ids"]), travel_mode=TravelMode(str(arguments["travel_mode"])))
            optimized = self.service.optimize_route(tuple(str(item) for item in arguments["place_ids"]), travel_mode=TravelMode(str(arguments["travel_mode"])))
            return {"planned": planned, "provider_calculated": optimized}, {"route_kinds": [planned.route_kind.value, optimized.route_kind.value]}
        if name == "geo.route.save":
            route = _route_from_dict(arguments["route"])
            saved = self.service.save_route(route)
            return {"route": saved}, {"route_id": saved.route_id, "status": saved.status.value}
        if name == "geo.route.get":
            route = self.service.repository.get_route(str(arguments["route_id"]))
            return {"route": route}, {"route_id": route.route_id}
        if name == "geo.track.ingest":
            track = _track_from_dict(arguments["track"])
            saved = self.service.ingest_track(track, idempotency_key=str(arguments["idempotency_key"]))
            return {"track": saved}, {"track_id": saved.track_id, "point_count": len(saved.points)}
        if name == "geo.track.query":
            tracks = (self.service.repository.get_track(str(arguments["track_id"])),) if arguments.get("track_id") else self.service.repository.list_tracks()
            return {"tracks": tracks}, {"result_count": len(tracks)}
        if name == "geo.track.simplify":
            track = self.service.simplify_track(str(arguments["track_id"]), tolerance_m=float(arguments.get("tolerance_m", 25.0)))
            return {"track": track}, {"track_id": track.track_id, "point_count": len(track.points), "derived": True}
        if name == "geo.visit.detect_candidate":
            visit = self.service.detect_visit_candidate(track_id=str(arguments["track_id"]), place_id=str(arguments["place_id"]), radius_m=float(arguments.get("radius_m", 100.0)))
            return {"visit": visit}, {"visit_id": visit.visit_id, "status": visit.status}
        if name == "geo.visit.confirm":
            visit = self.service.confirm_visit(str(arguments["visit_id"]))
            return {"visit": visit}, {"visit_id": visit.visit_id, "status": visit.status}
        if name == "geo.visit.history":
            visits = self.service.repository.list_visits(place_id=_optional_str(arguments, "place_id"), status=_optional_str(arguments, "status"))
            return {"visits": visits}, {"result_count": len(visits)}
        if name == "geo.provider.geocode":
            provider = self._provider_for(arguments)
            if not hasattr(provider, "geocode"):
                raise GeoProviderUnavailable(str(arguments["provider"]))
            result = provider.geocode(str(arguments["address"]))
            return {"geocode": result}, {"provider": str(arguments["provider"]), "confidence": result.provenance.confidence}
        if name == "geo.provider.reverse_geocode":
            provider = self._provider_for(arguments)
            if not hasattr(provider, "reverse_geocode"):
                raise GeoProviderUnavailable(str(arguments["provider"]))
            result = provider.reverse_geocode(_coordinate(arguments["coordinate"]))
            return {"geocode": result}, {"provider": str(arguments["provider"]), "confidence": result.provenance.confidence}
        if name == "geo.provider.route":
            provider = self._provider_for(arguments)
            if not hasattr(provider, "optimize_route"):
                raise GeoProviderUnavailable(str(arguments["provider"]))
            result = provider.optimize_route(tuple(str(item) for item in arguments["place_ids"]), TravelMode(str(arguments["travel_mode"])))
            return {"provider_route": result}, {"provider": result.provider, "stop_count": len(result.ordered_place_ids)}
        if name == "geo.open.external_navigation":
            place = self.service.repository.get_place(str(arguments["place_id"]))
            url = build_external_navigation_url(place, provider=str(arguments.get("provider", "google_maps")))
            return {"place_id": place.place_id, "navigation_url": url}, {"place_id": place.place_id, "external_mutation": False}
        raise GeoMcpValidationError("UNSUPPORTED_TOOL")

    def _provider_for(self, arguments: Mapping[str, object]) -> object:
        if self.provider is None:
            raise GeoProviderUnavailable(str(arguments.get("provider", "unknown")))
        requested = arguments.get("provider")
        if requested is not None and requested != getattr(self.provider, "provider", requested):
            raise GeoProviderUnavailable(str(requested))
        return self.provider

    def _project(self, value: object) -> object:
        if self.allow_private_read:
            return _serialize(value, include_private=True)
        return _serialize(value, include_private=False)


def build_external_navigation_url(place: Place, *, provider: str = "google_maps") -> str:
    if provider != "google_maps":
        raise GeoError("PROVIDER_NOT_SUPPORTED")
    if place.coordinate is not None:
        query = f"{place.coordinate.lat:.6f},{place.coordinate.lon:.6f}"
    elif place.normalized_address:
        query = place.normalized_address
    else:
        query = place.canonical_name
    return "https://www.google.com/maps/dir/?api=1&" + urlencode({"destination": query})


def handle_jsonrpc_message(message: Mapping[str, object], *, dispatcher: GeoMcpDispatcher) -> dict[str, object]:
    """Handle MCP JSON-RPC messages using the existing Skeleton server lifecycle."""

    message_id = message.get("id")
    method = message.get("method")
    try:
        if not isinstance(method, str):
            raise GeoMcpValidationError("METHOD_REQUIRED")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": message_id, "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}, "capabilities": {"tools": {}}}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": message_id, "result": {"tools": list(dispatcher.list_tools())}}
        if method == "tools/call":
            params = message.get("params")
            if not isinstance(params, Mapping) or not isinstance(params.get("name"), str):
                raise GeoMcpValidationError("TOOL_NAME_REQUIRED")
            arguments = params.get("arguments", {})
            result = dispatcher.call_tool(str(params["name"]), arguments if isinstance(arguments, Mapping) else None)
            return {"jsonrpc": "2.0", "id": message_id, "result": result}
        raise GeoMcpValidationError("METHOD_NOT_SUPPORTED")
    except GeoError as exc:
        return {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32602, "message": exc.reason_code}}


def _validate_schema(schema: Mapping[str, object], value: object, path: str = "arguments") -> None:
    if not isinstance(schema, Mapping):
        raise GeoMcpValidationError("SCHEMA_INVALID")
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if value is None and "null" in schema_type:
            return
        schema_type = next((item for item in schema_type if item != "null"), None)
    if schema_type == "object":
        if not isinstance(value, Mapping):
            raise GeoMcpValidationError("ARGUMENT_OBJECT_REQUIRED")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise GeoMcpValidationError("SCHEMA_INVALID")
        for required in schema.get("required", ()):
            if required not in value:
                raise GeoMcpValidationError("REQUIRED_FIELD_MISSING")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise GeoMcpValidationError("UNKNOWN_FIELD")
        for key, child in value.items():
            if key in properties:
                _validate_schema(properties[key], child, f"{path}.{key}")
        return
    if schema_type == "array":
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise GeoMcpValidationError("ARRAY_REQUIRED")
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise GeoMcpValidationError("ARRAY_TOO_SHORT")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise GeoMcpValidationError("ARRAY_ITEMS_NOT_UNIQUE")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for item in value:
                _validate_schema(item_schema, item, path)
        return
    if schema_type == "string":
        if not isinstance(value, str):
            raise GeoMcpValidationError("STRING_REQUIRED")
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise GeoMcpValidationError("STRING_TOO_SHORT")
        if "enum" in schema and value not in schema["enum"]:
            raise GeoMcpValidationError("ENUM_VALUE_INVALID")
        return
    if schema_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GeoMcpValidationError("NUMBER_REQUIRED")
        if "minimum" in schema and float(value) < float(schema["minimum"]):
            raise GeoMcpValidationError("NUMBER_BELOW_MINIMUM")
        if "maximum" in schema and float(value) > float(schema["maximum"]):
            raise GeoMcpValidationError("NUMBER_ABOVE_MAXIMUM")
        return
    if schema_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise GeoMcpValidationError("INTEGER_REQUIRED")
        if "minimum" in schema and value < int(schema["minimum"]):
            raise GeoMcpValidationError("INTEGER_BELOW_MINIMUM")
        return
    raise GeoMcpValidationError("SCHEMA_TYPE_UNSUPPORTED")


def _coordinate(value: object) -> Coordinate:
    if not isinstance(value, Mapping):
        raise GeoMcpValidationError("COORDINATE_REQUIRED")
    return Coordinate.from_dict(value)


def _optional_str(arguments: Mapping[str, object], key: str) -> str | None:
    value = arguments.get(key)
    return str(value) if isinstance(value, str) else None


def _route_from_dict(value: object) -> Route:
    if not isinstance(value, Mapping):
        raise GeoMcpValidationError("ROUTE_OBJECT_REQUIRED")
    stops_value = value.get("stops")
    if not isinstance(stops_value, Sequence) or isinstance(stops_value, (str, bytes)):
        raise GeoMcpValidationError("ROUTE_STOPS_REQUIRED")
    stops = tuple(RouteStop(str(item["place_id"]), int(item["sequence"]), int(item.get("dwell_seconds", 0))) for item in stops_value if isinstance(item, Mapping))
    return Route(
        route_id=str(value["route_id"]),
        route_kind=RouteKind(str(value["route_kind"])),
        stops=stops,
        travel_mode=TravelMode(str(value["travel_mode"])),
        distance_m=float(value["distance_m"]) if value.get("distance_m") is not None else None,
        duration_s=int(value["duration_s"]) if value.get("duration_s") is not None else None,
        provider=str(value["provider"]) if isinstance(value.get("provider"), str) else None,
        provenance=Provenance.from_dict(value.get("provenance", {"source_kind": "local_planner", "confidence": 1.0})),
        status=RouteStatus(str(value.get("status", RouteStatus.DRAFT.value))),
        associated_ref=str(value["associated_ref"]) if isinstance(value.get("associated_ref"), str) else None,
        saved_at=str(value.get("saved_at", utc_now())),
        updated_at=str(value.get("updated_at", utc_now())),
    )


def _track_from_dict(value: object) -> Track:
    if not isinstance(value, Mapping):
        raise GeoMcpValidationError("TRACK_OBJECT_REQUIRED")
    points_value = value.get("points")
    if not isinstance(points_value, Sequence) or isinstance(points_value, (str, bytes)):
        raise GeoMcpValidationError("TRACK_POINTS_REQUIRED")
    points = []
    for item in points_value:
        if not isinstance(item, Mapping):
            raise GeoMcpValidationError("TRACK_POINT_INVALID")
        points.append(TrackPoint(str(item["timestamp"]), _coordinate(item["coordinate"]), float(item["accuracy_m"]) if item.get("accuracy_m") is not None else None, float(item["speed_mps"]) if item.get("speed_mps") is not None else None, float(item["heading_deg"]) if item.get("heading_deg") is not None else None))
    return Track(
        track_id=str(value["track_id"]),
        points=tuple(points),
        source_device_ref=str(value["source_device_ref"]),
        retention=TrackRetention(str(value["retention"])),
        created_at=str(value.get("created_at")) if value.get("created_at") else Track.__dataclass_fields__["created_at"].default_factory(),
        derived_geometry=tuple(_coordinate(item) for item in value.get("derived_geometry", ()) if isinstance(item, Mapping)),
    )


def _serialize(value: object, *, include_private: bool) -> object:
    if isinstance(value, Place):
        return value.to_dict(include_private=include_private)
    if isinstance(value, Route):
        return value.to_dict(include_private=include_private)
    if isinstance(value, Track):
        return value.to_dict(include_private=include_private)
    if isinstance(value, VisitRecord):
        return {
            "visit_id": value.visit_id,
            "place_id": value.place_id,
            "started_at": value.started_at,
            "ended_at": value.ended_at,
            "source_track_id": value.source_track_id,
            "confidence": value.confidence,
            "status": value.status,
            "provenance": value.provenance.to_dict(),
        } if include_private else {"visit_id": value.visit_id, "place_id": value.place_id, "confidence": value.confidence, "status": value.status, "privacy_class": PRIVATE_GEO}
    if hasattr(value, "to_dict"):
        return value.to_dict(include_private=include_private)
    if isinstance(value, ProviderRouteResult):
        return {
            "ordered_place_ids": list(value.ordered_place_ids),
            "distance_m": value.distance_m,
            "duration_s": value.duration_s,
            "provider": value.provider,
            "provenance": value.provenance.to_dict(),
        }
    if isinstance(value, Mapping):
        return {str(key): _serialize(item, include_private=include_private) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serialize(item, include_private=include_private) for item in value]
    return value


def _ok(spec: ToolSpec, payload: object, receipt: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema": GEO_MCP_SCHEMA,
        "tool": spec.name,
        "status": "OK",
        "side_effect_class": spec.side_effect_class,
        "payload": payload,
        "receipt": {
            "status": "APPLIED" if spec.side_effect_class == "private_local_mutation" else "READ",
            "privacy_class": PRIVATE_GEO,
            **dict(receipt),
        },
    }


def _blocked(reason_code: str, *, tool: str, side_effect_class: str = "read_only") -> dict[str, object]:
    return {
        "schema": GEO_MCP_SCHEMA,
        "tool": tool,
        "status": "BLOCKED",
        "reason_code": reason_code,
        "side_effect_class": side_effect_class,
        "receipt": {"status": "BLOCKED", "privacy_class": PRIVATE_GEO, "reason_code": reason_code},
    }
