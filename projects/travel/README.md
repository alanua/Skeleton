# Travel Baseline

This directory links Skeleton to the public-safe implementation repository `alanua/Travel`.

## Scope

The canonical public-safe module inventory is `alanua/Travel/MODULES.yaml`. It covers:

- rolling worldwide annual opportunity map;
- weekend and day-trip radar;
- shortlist and booking-ready proposal modes;
- trip, itinerary and lifecycle semantics;
- Offer Intelligence;
- door-to-door access, airport and surface-transport policy;
- reusable national, regional, urban, coach, ferry, airline, airport and standard-feed adapters;
- live journey projections and disruption handling;
- stay, package and activity intelligence;
- total cost, useful time, scoring and false-bargain rejection;
- provider registry, evidence, private warehouse and price-history policies;
- document and risk gates;
- private personalization projection, monitoring and output contracts;
- trip-pack and approved booking-record semantics.

## Skeleton boundary

Travel owns travel-specific entities, lifecycle, policies and composition recipes. It consumes shared Skeleton capabilities for calendars, schedules, tasks, acquisition, parsers, evidence, artifacts, maps, routing, tracker reads, documents, finance, weather, situational intelligence, memory, secrets, notifications, Runner, Loop and approvals.

It must not duplicate those shared systems.

## Source architecture

- `alanua/Skeleton#1750` — general modular architecture;
- `alanua/Skeleton#1748` — Travel domain composition;
- `alanua/Skeleton#1747` — Offer Intelligence;
- `alanua/Skeleton#1749` — public-safe fixture slice;
- `alanua/Skeleton#1545` — bounded WorldMonitor sensor;
- `alanua/Skeleton#1761` — secret-store boundary;
- `alanua/Travel#1` — Travel repository architecture baseline.

## Privacy boundary

The public project tree must not contain personal travel history, preferences, budgets, watchlists, rankings, route candidates, private price history, document status, bookings, tickets, raw tracks, private calendars, payment data or family data.

Secrets and credentials belong only in the approved Secret Store Gate. Sensitive files belong in encrypted artifact storage. Private planning state belongs behind MemoryGateway and is canonical only after approved write and read-back.

## Repository route

```text
Skeleton PROJECT_INDEX.yaml
→ projects/travel/PROJECT_MANIFEST.yaml
→ alanua/Travel/PROJECT_MANIFEST.yaml
→ alanua/Travel/MODULES.yaml
```

Provider adapters are created as real planning needs arise, validated on bounded routes and retained for reuse.

Current status: public-safe architecture and module inventory are documented; live adapters, private runtime integration and booking authority are not yet implemented.
