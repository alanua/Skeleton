# Travel Baseline

This directory links Skeleton to the public-safe implementation repository `alanua/Travel`.

## Scope

- Worldwide travel opportunity discovery and comparison.
- Weekend and short-trip discovery.
- Multimodal route planning from a declared home base.
- Reusable transport and provider adapters.
- Live journey monitoring and disruption handling.
- Public-safe architecture, schemas, source metadata, licences, and synthetic tests.

## Privacy boundary

The public project tree must not contain personal travel history, preferences, budgets, watchlists, rankings, route candidates, document status, private price observations, bookings, tickets, location tracks, or family data.

Secrets and credentials belong only in the approved secret store. Private planning state belongs behind the Skeleton Memory Gate.

## Repository route

```text
Skeleton project index
→ projects/travel/PROJECT_MANIFEST.yaml
→ alanua/Travel/PROJECT_MANIFEST.yaml
```

Provider adapters are created as real planning needs arise, validated on bounded routes, and retained for reuse.
