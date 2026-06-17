# Hermes Private Memory Bridge

Hermes private-memory bridge contract v0 is a public-safe adapter over the
existing Skeleton private-memory connector. It gives Hermes a stable boundary for
orientation and synthetic heartbeat or note writes without exposing local
private memory values.

This is code and test foundation only. It does not wire Hermes runtime services,
enable providers, run Aufmass, ingest project data, or publish private values.

## Contract

- Module: `core/hermes_private_memory.py`
- Public report schema: `skeleton.hermes_private_memory.report.v0`
- Connector boundary: `core/private_memory.py`
- Default operation: read-only orientation
- Write operations: blocked unless the caller passes an explicit write gate

The bridge exposes three public-safe calls:

- `orient_hermes_private_memory(...)`
- `write_hermes_private_memory_heartbeat(..., write_enabled=True)`
- `record_hermes_private_memory_note(..., write_enabled=True)`

Orientation is read-first and must not create or modify the private SQLite
database. Heartbeat and note writes use the existing connector heartbeat and
task-state heartbeat APIs. The bridge never opens SQLite directly.

## Public Report Fields

Reports may contain only:

- schema and operation tokens
- connector status tokens
- aggregate connector booleans
- write-gate state
- error class names
- next-action tokens

Reports must not contain local paths, registry values, table names, SQL text,
row payloads, raw memory content, secrets, provider output, Drive IDs, Telegram
IDs, Aufmass quantities, or project data.

## Write Gate

Hermes writes are blocked by default. A caller must explicitly pass
`write_enabled=True` for synthetic heartbeat or note writes. A blocked write
returns `BLOCKED` with `HermesPrivateMemoryWriteGateError` and the next-action
token `operator_enable_hermes_private_memory_write`.

The bridge accepts only synthetic public-safe IDs and short public-safe state
tokens. It rejects private-looking values before calling the connector.

## Privacy Boundary

GitHub may contain this bridge code, documentation, and synthetic tests. Real
Hermes memory usage, local config, local registry, database paths, private
records, and project data remain local/private.

The validation suite uses temporary synthetic config files and synthetic SQLite
databases only.
