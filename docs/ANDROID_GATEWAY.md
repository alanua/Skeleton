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

Providers are opaque identifiers. The gateway does not import provider SDKs, call Android APIs, open device connections, deploy apps, mutate runtime state, or collect private topology.

## Privacy Boundary

Payloads are rejected when keys indicate sensitive device identifiers, credentials, contact details, location, notification or message text, raw logs, app content, runtime details, or mutation requests.

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

Invalid or sensitive events are represented as rejected receipts in the snapshot rather than being routed anywhere.
