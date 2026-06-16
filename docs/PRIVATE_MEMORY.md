# Private Memory Connector

Skeleton private memory is a server-local SQLite boundary. The public repository may
contain connector code, synthetic tests, and sanitized status fields only. It must
not contain a real database, real registry values, local server paths, secrets, raw
task payloads, provider outputs, Drive identifiers, or private project data.

The connector in `core/private_memory.py` is a foundation for later Runner,
Hermes, and OpenHands routing. This task does not wire it into the Runner
dispatcher and does not enable live provider or model routing.

## Boundary

- GitHub receives only public-safe aggregate status such as whether a database is
  configured, openable, integrity-checked, schema-ready, and writable when an
  explicit write request is made.
- The real SQLite database path and registry file remain local to the server.
- Healthcheck output must not include raw paths, table names, SQL text, row
  payloads, secrets, environment values, or private registry values.
- Missing config, invalid config, failed integrity checks, schema mismatch,
  write failure, and privacy violations fail closed with `BLOCKED` status.
- Default healthchecks are read-only and must not create or modify a database.
- SQLite read/write access is opened only when the caller explicitly requests a
  write operation.

## Config

Callers pass an explicit local config path or set `SKELETON_PRIVATE_MEMORY_CONFIG`
in the server environment. The config is intentionally local-only and should use
the schema `skeleton.private_memory.config.v0`.

The public repo must not commit a real config file. Tests create temporary
synthetic config files and temporary synthetic SQLite databases only.

## Supported Public-Safe Operations

- Read-only healthcheck with aggregate status.
- Explicit write-mode schema initialization for the connector's own heartbeat
  storage.
- Public-safe heartbeat write/read using synthetic IDs.
- Public-safe task-state heartbeat recording using synthetic task IDs.

These operations are enough to prove the connector boundary without exposing
private memory or wiring runtime dispatch.

## Current Project State

Aufmass execution is paused. The private memory connector is present only as a
public-safe foundation for future Runner, Hermes, and OpenHands integration.
