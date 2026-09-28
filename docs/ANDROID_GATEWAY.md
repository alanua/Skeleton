# Android Gateway

`core/skeleton_android_gateway.py` defines the first provider-neutral Android telemetry gateway slice. It is intentionally a public-safe metadata contract, not an Android runtime integration.

## Contract

- Event schema: `skeleton.android_gateway.telemetry_event.v1`
- Receipt schema: `skeleton.android_gateway.receipt.v1`
- Snapshot schema: `skeleton.android_gateway.snapshot.v1`
- Contract version: `1.0.0`

The gateway accepts bounded JSON metadata with:

- `event_id`
- `provider`
- `event_type`
- `observed_at`
- `payload`

Events fail closed when they contain fields outside this schema. Android runtime details, raw app content, mutation requests, logs, files, intents, and device identifiers must not be supplied as adjacent top-level fields or nested payload keys.

Providers are opaque identifiers. The gateway does not import provider SDKs, call Android APIs, open device connections, deploy apps, mutate runtime state, or collect private topology.

Accepted receipts include the normalized event digest, `privacy_boundary: public_safe_metadata_only`, and `runtime_mutation: false` so callers can audit that the gateway stayed inside the v1 public-safe metadata contract.

## Privacy Boundary

Payloads are rejected when keys indicate sensitive device identifiers, credentials, contact details, location, notification or message text, raw logs, app content, runtime details, intents, files, or mutation requests. The key checks are case-insensitive and cover common delimiter and camelCase forms such as `raw_logcat`, `rawLogcat`, `app_content`, and `appContent`. Public-safe metric names such as `latency_ms` remain valid; location fields such as `lat`, `lon`, and `location` fail closed. Non-finite numbers and over-deep objects or arrays are rejected because accepted payloads must be bounded JSON-safe metadata.

Accepted snapshots include:

- aggregate provider counts
- aggregate event-type counts
- accepted public-safe events
- rejected receipt summaries
- `runtime_mutation: false`

## Snapshot CLI

Render a deterministic JSON snapshot from one or more event files:

```bash
python3 scripts/android_gateway_snapshot.py --event event.json
```

The CLI defaults `generated_at` to `1970-01-01T00:00:00Z` so repeated runs over the same event files produce identical output. Pass `--generated-at` to embed a specific review timestamp.

Invalid or sensitive events are represented as rejected receipts in the snapshot rather than being routed anywhere.
