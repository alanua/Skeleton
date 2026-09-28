# Android Gateway

`core/skeleton_android_gateway.py` defines the shared local-first Android Gateway v1. It is not a second control plane, remote executor, upload path, or Android runtime mutation surface.

## Contract

- Observation schema: `skeleton.android.observation.v1`
- Snapshot schema: `skeleton.android.snapshot.v1`
- Contract version: `1.0.0`

Each observation is a typed `AndroidObservation` with:

- `observation_id`
- `source`
- `node_id`
- `observed_at`
- `kind`
- `quality`
- `confidence`
- `payload`
- `provenance`

`observation_id` is deterministic from canonical normalized content. The gateway does not use UUIDs or random IDs for observation identity.

The v1 kind set is intentionally strict:

- `battery`
- `storage`
- `runtime`
- `network`
- `supervisor`
- `remote_desktop_state`

Arbitrary provider/app events such as `screen.rendered`, `app.started`, and generic provider event types are not accepted. Remote desktop state is limited to `ONLINE`, `OFFLINE`, `AUTH_REQUIRED`, and `UNKNOWN`.

## Local Collection

`scripts/android_gateway_snapshot.py` runs the local collector and emits a `skeleton.android.snapshot.v1` JSON snapshot to stdout. It also supports `--output PATH` for writing the same snapshot to disk.

Observation persistence is optional and local-only:

```bash
python3 scripts/android_gateway_snapshot.py --sqlite android_gateway.sqlite3
```

The store uses Python `sqlite3`, creates its local schema safely, and uses `observation_id` as the primary key so re-ingesting the same normalized observation is idempotent. Query helpers expose `latest(kind=None)`, `by_kind(kind, limit=...)`, and `recent(limit=...)`.

The collector only uses explicit allowlisted local evidence:

- `termux-battery-status` for battery level, status, charging state, and temperature when available
- local filesystem APIs for storage capacity and usage
- local uptime/basic process health for runtime
- coarse network availability only
- bounded local process evidence for supervisor state
- local hold/process evidence for remote desktop state

Missing Termux commands degrade to unavailable or unknown observations instead of crashing.

## Privacy Boundary

Payload and provenance are recursively validated and fail closed. The gateway rejects SSID, BSSID, IP/MAC identifiers, location fields, clipboard data, SMS/call/contact material, notification/message bodies, credentials, auth/2FA/token/cookie/password material, finance/banking data, messenger content, microphone/audio/photo references, raw logcat, files, intents, mutations, and arbitrary app content.

Accepted JSON must be bounded, finite, deterministic, and canonicalizable. Network collection may inspect local Termux network data internally, but SSID/BSSID/IP/MAC fields are discarded and must not appear in emitted payload or provenance.

The gateway does not perform SSH, HTTP, uploads, remote execution, clipboard reads, notification reads, app-private scraping, MemoryGate writes, or network persistence.

## Adapter Direction

The Home APK is a future adapter surface only. It should feed this shared local-first gateway when that adapter exists; it should not become a separate control plane.

Future adapters explicitly include:

- Health Connect
- Sleep as Android
- Reading
- Media
- Calendar

Raw and high-frequency observations stay local. MemoryGate may later receive only separately bounded derived summaries under explicit domain policy.
