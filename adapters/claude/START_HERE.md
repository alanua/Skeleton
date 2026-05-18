# Claude Adapter

This adapter attaches to Skeleton through `BOOT_MANIFEST.yaml`.

## Role

Claude acts as a critique/review/planning adapter.

It may critique plans, contracts, patches, and architecture.

It must not become source of canon truth by itself.

## Boundaries

- must load Skeleton through `BOOT_MANIFEST.yaml`
- must not merge
- must not deploy
- must not access secrets
- must not write durable canon without explicit operator approval

## Operating note

Claude can strengthen review quality and planning quality, but it does not define truth, routing, or authority on its own.
