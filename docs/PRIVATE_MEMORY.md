# Private Memory Connector

Skeleton private memory is a server-local SQLite boundary. The public repository may
contain connector code, synthetic tests, and sanitized status fields only. It must
not contain a real database, real registry values, local server paths, secrets, raw
task payloads, provider outputs, Drive identifiers, or private project data.

The connector in `core/private_memory.py` is a foundation for later Runner,
Hermes, and OpenHands routing. The Runner now has a bounded maintenance
healthcheck that reaches this connector through local server config only. This
does not wire Hermes runtime, execute Aufmass, or enable live provider or model
routing.

## Boundary

- GitHub receives only public-safe aggregate status such as whether a database is
  configured, openable, integrity-checked, schema-ready, and writable when an
  explicit write request is made.
- The real SQLite database path and registry file remain local to the server.
- Healthcheck output must not include raw paths, table names, SQL text, row
  payloads, secrets, environment values, or private registry values.
- Missing config, invalid config, invalid registry values, invalid database
  paths, failed integrity checks, schema mismatch, write failure, and privacy
  violations fail closed with `BLOCKED` status.
- Default healthchecks are read-only and must not create or modify a database.
- SQLite read/write access is opened only when the caller explicitly requests a
  write operation.
- Runner maintenance reports may include only aggregate booleans, counts, status,
  error class names, and next-action tokens from the connector report.

## Config

Callers pass an explicit local config path or set `SKELETON_PRIVATE_MEMORY_CONFIG`
in the server environment. The config is intentionally local-only and should use
the schema `skeleton.private_memory.config.v0`.

The explicit connector config has a `database.path` value. That path can be
absolute on the server or relative to the config file. It is read only inside the
connector and is never included in public reports.

The connector also accepts a server-local bootstrap registry adapter for the
already-created local registry format. Supported registry schemas are:

- `skeleton.bootstrap.local_registry.v0`
- `skeleton.local_registry.v0`
- `skeleton.private_memory.local_registry.v0`

For those schemas, the adapter looks for the private SQLite entry under the local
private-memory service or connector section, then normalizes the configured
database path relative to the registry root when one is present. This preserves
the bootstrap/local-registry setup without creating a second connector format.

The public repo must not commit a real config file, real local registry, or real
SQLite database. Tests create temporary synthetic config files, synthetic registry
files, and temporary synthetic SQLite databases only. The real server registry and
database path stay local on Hetzner and are never committed.

## Supported Public-Safe Operations

- Read-only healthcheck with aggregate status.
- Explicit write-mode schema initialization for the connector's own heartbeat
  storage.
- Public-safe heartbeat write/read using synthetic IDs.
- Public-safe task-state heartbeat recording using synthetic task IDs.
- Cross-project registry summarization through
  `core/private_project_memory.py`, limited to public-safe aggregate counts and
  next-action tokens.

These operations are enough to prove the connector boundary without exposing
private memory or wiring runtime dispatch.

## Relationship To Graph Memory

`docs/GRAPH_MEMORY.md` defines Graphify as a future private derived graph-memory
layer. The SQLite private memory connector remains the canonical local project
memory store. Graphify-derived nodes, edges, embeddings, labels, traversals, and
summaries are cache/index state only: they may support orientation and review,
but they do not outrank SQLite canonical memory, current GitHub state, protected
repo rules, or human approval.

Public reports about graph memory may contain only synthetic query identifiers,
allowlisted status tokens, aggregate counts, error class names, and next-action
tokens. Real graph outputs, private records, local paths, secrets, Drive IDs,
Telegram IDs, customer data, Aufmass quantities, and Graphify payloads stay
local/private.

## Current Project State

Aufmass execution is paused. Runner wiring is limited to the
`private_memory_healthcheck` maintenance task. Hermes runtime wiring, worker
routing, private task-state retrieval, real project memory ingestion, and
Aufmass use of private memory remain later tasks.
