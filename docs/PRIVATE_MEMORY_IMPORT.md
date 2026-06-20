# Private Memory Seed Import

`private_memory_seed_import_v1` is a bounded Runner maintenance task for an
operator-staged local ZIP seed package. It is not a generic ingestion path and
does not read paths or records from issue text.

The task is blocked unless the fenced task payload opens the explicit write gate:

````text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: private_memory_seed_import_v1

```task
write_gate=private_memory_seed_import_v1
```
````

The Runner resolves both the target private SQLite database and the staged seed
ZIP through the local private memory config or bootstrap registry boundary. The
public report contains only aggregate status and count fields.

## Seed ZIP

The ZIP may contain only:

1. `manifest.json`
2. `records.sqlite`

The manifest schema is `skeleton.private_memory_seed.manifest.v1` with
`manifest_version` set to `1`. It declares bounded record and status-history
counts and a SHA-256 checksum for `records.sqlite`.

The staged SQLite file is opened read-only and must pass `PRAGMA
integrity_check`. It must contain exactly the expected seed tables:

1. `seed_records`
2. `seed_status_history`

Unsafe archive names, extra files, oversized members, checksum mismatches,
unknown manifest versions, unexpected SQLite schemas, and unsafe payload classes
are reported as `BLOCKED`.

## Import Rules

The importer creates a SQLite snapshot before writing and imports in one
transaction. It writes only dedicated namespace tables:

1. `private_memory_import_batches`
2. `private_memory_import_records`
3. `private_memory_import_status_history`
4. `private_memory_import_audit`

Existing connector tables are not modified. A failed import rolls back fully.
Repeating the same package is idempotent.

No raw config paths, staged paths, database paths, row payloads, graph labels,
source locators, SQL text, secrets, or private content are emitted in public
Runner output.

## Derived Graph Index

`core.graph_memory_index` rebuilds local derived JSON and GraphML indexes from
the canonical imported records. The graph builder opens canonical private memory
read-only and writes only derived files at the configured output directory. It
does not write back to canonical memory.

Actual runtime import requires a separate operator-approved maintenance issue
after the seed package has been staged locally.
