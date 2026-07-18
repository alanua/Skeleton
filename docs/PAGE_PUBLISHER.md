# Skeleton Page Publisher

Skeleton Page Publisher is the reusable controlled publication engine for validated static pages. Domain modules render page packages; Publisher verifies, publishes, rolls back and returns an encrypted result receipt.

## Ownership boundary

Skeleton owns encrypted handoff, profile registry, package validation, backend-state checks, atomic filesystem publication, route mutation, verification, rollback, cleanup and encrypted receipts.

Domain modules own templates, content schemas, rendering and downstream actions. Travel is the first profile and owns `/travel`, but the Publisher core contains no Milan, itinerary, price, calendar or Journaway logic.

## Profiles

The first profile is `travel_private_v1`:

- namespace `/travel`;
- tailnet-private visibility;
- Tailscale Serve static backend;
- page ID supplied by the caller; final path derived by the profile.

## Runtime task IDs

Canonical IDs:

- `prepare_page_publication_handoff`
- `publish_static_page`

Historic IDs remain temporary Runner aliases only:

- `prepare_private_static_site_handoff`
- `deploy_private_static_site`

## Safety

- backend state is fail-closed;
- an already configured exact path is never overwritten;
- no Funnel, global Serve reset, firewall change or public port is used;
- prior site and metadata remain recoverable until handoff secret cleanup succeeds;
- private hostname and URL are returned only inside the caller-encrypted result capsule;
- inline Base64 is a small-payload compatibility transport capped by profile; larger artifacts require a private artifact adapter.
