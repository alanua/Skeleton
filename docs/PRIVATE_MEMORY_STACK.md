# Private Memory Stack

Skeleton private memory is a local-only stack for an operator server. SQLite is the only authority. Graphify and MemPalace are derived local indexes rebuilt from active SQLite facts.

Aufmass is out of scope.

## Install

```bash
bash scripts/install_skeleton_private_memory.sh
```

The installer creates `~/.local/bin/skeleton-memory`, runs a private smoke test with synthetic temporary values, deletes the temporary fact, and rebuilds indexes. It does not start a service, open a port, contact a provider, request credentials, or upload data.

Set a private root when needed:

```bash
export SKELETON_PRIVATE_MEMORY_ROOT="$HOME/.local/share/skeleton-private-memory"
```

## Commands

```bash
skeleton-memory init
skeleton-memory put skeleton.notes note1 --json '{"summary":"local fact","tags":["ops"]}'
skeleton-memory get skeleton.notes note1
skeleton-memory search "local fact" --limit 5
skeleton-memory relations ops --limit 5
skeleton-memory rebuild
skeleton-memory backup --snapshot-id snapshot-001
skeleton-memory status
```

`put`, `delete`, and `import-bundle` enter the private `MemoryGateway` compatibility boundary before they reach the stack. The gateway request preserves operator approval, actor, reason, project scope, expected revision, idempotency key, source hash, and bundle hash metadata where applicable. `get` is exact and authoritative because it reads canonical SQLite directly. `search` and `relations` are non-authoritative derived results and include canonical refs and revisions for exact confirmation.

## Storage

All runtime files stay under one configured private root:

- `canonical.sqlite`: canonical private memory database
- `mempalace.index.json`: derived semantic index
- `graphify.index.json`: derived relationship index
- `backups/*.sqlite`: local SQLite snapshots
- `memory_gateway_import.sqlite`: local receipt database for the approved Memory Gateway manifest import path

The private root is created with mode `0700`. SQLite and derived files are written with mode `0600`. SQLite WAL and integrity checks are enabled where supported.

## Authority

Canonical facts and history are preserved. Initialization validates a non-empty existing database and never recreates or overwrites a valid one. The approved `fast_autonomous_execution_v1` manifest is imported idempotently through the Memory Gateway path and mirrored into the private SQLite authority.

After every successful canonical put or delete, both derived indexes are rebuilt from the same active SQLite facts before success is reported. The canonical mutation and both derived index rebuilds run under one inter-process exclusive lock so concurrent writers cannot observe a half-rebuilt stack.

If an index rebuild fails after a canonical mutation is committed, canonical SQLite remains the sole authority and the CLI reports a degraded receipt with the affected derived index names. Retry uses the gateway idempotency record and canonical transaction reference, so a crash after canonical commit cannot write a second mutation or advance the revision twice. Rollback for pre-commit canonical failures uses a logical SQLite backup made through SQLite's backup API instead of raw database bytes and removes stale `-wal` and `-shm` sidecars before reporting the mutation as blocked.

Graphify and MemPalace never write canonical SQLite directly.

## Status

`skeleton-memory status` reports only aggregate counts and `READY`, `STALE`, or `BLOCKED` states. Local Graphify and MemPalace index loads and status checks validate the stored `index_hash` and aggregate counts before reporting readiness. Empty derived-index queries are rejected. Stale derived indexes remain non-authoritative and cannot support write proposals.

Status output does not print private values, local paths, records, index contents, or backup contents.
