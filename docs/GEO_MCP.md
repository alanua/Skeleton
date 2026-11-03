# Skeleton Geo MCP v1

## Scope

`geo` is a shared Skeleton capability, not a Travel or Android-owned database. It
defines provider-neutral place, route, track and visit contracts. Travel, BauClock,
Gewerbe, Life Archive and Home Automation consume the capability through bounded
IDs and domain references.

The v1 code slice contains:

- normalized `Place`, `Route`, `Track`, `VisitRecord` and provenance models;
- private-safe idempotent place state and synthetic in-memory repository;
- category/tag, bounding-box and nearby queries;
- planned routes kept separate from provider-calculated routes and observed tracks;
- route optimization through an injected provider contract;
- track simplification, retention and visit-candidate/confirmation contracts;
- strict typed MCP tool schemas and fail-closed JSON-RPC dispatch;
- Google Places/Geocoding/Routes response normalization with an injected transport;
- keyless external Google Maps navigation URLs;
- public-safe receipts which never contain addresses, coordinates or raw track points.

`InMemoryGeoRepository` is test/contract infrastructure only. Production canonical
state must be supplied by the existing private-state/MemoryGateway boundary; this
slice does not create a second database, auth system, action gate or MCP server.

## MCP boundary

The dispatcher is a library surface for the existing Skeleton MCP runtime. The
protected runtime/registry wiring is intentionally a later operator-approved slice.
Tools are classified as:

| Class | Examples | Rule |
| --- | --- | --- |
| `read_only` | search, nearby, geocode, route calculation, list/query | no canonical mutation |
| `private_local_mutation` | save/update/archive/tag/rate, save route, ingest track | private state only; place/track writes require idempotency keys; route IDs are stable save keys |
| `external_mutation` | Google Saved List/My Maps changes | always separately gated; disabled in v1 |

`save_place` remains a compatibility alias for `geo.place.save`, so the #3711 chat
intake can be moved onto this contract without a second Places store. Repeated saves
deduplicate by provider identity first, then normalized address/coordinates. Name-only
matches are never silently merged.

## Privacy and evidence

Real places, coordinates, routes, tracks and visits are `PRIVATE_USER_GEO`. The
provider response is evidence with a retrieval timestamp and confidence; it is not
automatically canonical truth. Raw track retention is explicit (`raw_short`,
`derived_long`, `ephemeral`). A device location does not by itself prove user
presence; visit correlation starts as a candidate and requires confirmation before
the place becomes visited.

Public receipts expose only stable IDs, counts, status, confidence and operation
metadata. They do not expose addresses, coordinates, route geometry, source-device
references or raw points.

## Google adapter decision

The adapter targets the current documented Google Maps Platform surfaces:

- Places API (New): Text Search, Nearby Search and Place Details;
- Geocoding API: forward and reverse geocoding;
- Routes API: Compute Routes/Route Matrix as the future calculation provider;
- Maps URLs for external navigation, which do not require an API key.

The adapter accepts only a Bitwarden/Skeleton credential reference at runtime. No
API key, OAuth token or secret is present in source, fixtures, APK resources or
receipts. The exact runtime binding is intentionally named, not populated:
`Bitwarden:Skeleton/Google Maps Platform`.

Google personal Saved Lists and My Maps are not the authority. The documented
platform surfaces provide place/routing/geocoding data and navigation URLs, but no
supported core CRUD contract for a user's personal Saved Lists is used here. Any
future browser-side synchronization must be a separate, explicitly gated adapter;
it must not run when `geo.place.save` is called. The operator list `Де поїсти` is
not imported or mutated by this PR.

## #3711 and Android

The audit found #3711 `runner:blocked` with an unpublished worktree-only Android
implementation. Its useful semantics were bounded `save_place`, synthetic fixtures
and a keyless map/list consumer, but no branch/PR artifact was available to reuse.
This PR therefore establishes the shared backend contract first. The planned
Android `Місця / Мапа` surface remains a consumer of Geo MCP/API and must be
recovered or rebuilt in a later #3711 slice; it must not create another store.

## Staged follow-up

1. Bind the dispatcher to the existing MCP registry/runtime after protected review.
2. Replace the synthetic repository with the existing private-state adapter.
3. Recover #3711 Android UI against the shared contract.
4. Add provider request transport using the approved Bitwarden reference and fake
   provider tests; no live credential is required for the contract slice.
5. Add GeoJSON/KML/CSV import/export and richer route matrix/visit correlation.
6. Keep Saved List/My Maps automation outside core and separately gated.
