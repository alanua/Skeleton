# Memory Store

`core/memory_store.py` is the stage 1 local storage companion to the dry-run
memory manager. It writes only when a caller supplies an explicit filesystem
path. Importing it does not create ledgers, snapshots, directories, canon
records, runtime promotions, network requests, subprocesses, database
connections, or remote service calls.

## Local Audit Ledger

`build_memory_ledger_entry(record, route)` reuses `MemoryRecord` and
`MemoryRouteResult` from `core/memory_manager.py`. It records deterministic
routing metadata for one decision. `memory_ledger_entry_to_dict(entry)` exposes
the fixed field set documented by `schemas/memory_store.schema.json`.

`append_memory_ledger_entry(path, entry)` appends one compact deterministic JSON
object plus a newline to the explicit JSONL `path`. Existing entries stay in
append order. The function does not choose a default path or create a canon
write route.

The ledger never stores full record content. Public-safe records store a
whitespace-normalized preview bounded to 160 characters. Records where
`public_safe` is false store only:

```text
[REDACTED: non-public memory content]
```

The ledger still contains routing metadata such as record id, project id,
source, route status, audit summary, and blocked reason. Callers must keep that
metadata suitable for the local ledger path they choose.

## Session State Snapshots

`write_session_state_snapshot(path, snapshot)` writes deterministic sorted JSON
to the explicit `path`. Snapshot payloads must be JSON-safe mappings and must
declare `public_safe: true`. Keys that declare raw content, credentials,
secrets, or tokens are rejected recursively so the snapshot remains a state
summary instead of a raw memory-content store.

The snapshot schema documents the stage 1 public-safe shape:

- `schema`: `skeleton.memory_store.session_state_snapshot.v1`
- `public_safe`: true
- `session_id`
- `project_id`
- `state`

## Boundaries

This stage provides a local append-only audit ledger and an explicit snapshot
writer only. It does not alter memory routing, write canon, promote runtime
state, select default storage paths, read external state, use remote services,
or implement `memory_manager_live_storage`.
