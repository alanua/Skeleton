# Private Memory Recovery

Canonical private SQLite memory is revisioned and append-only. Public repository
evidence is limited to schemas, synthetic tests, aggregate counts, hashes, and
sanitized status fields.

## Contracts

- `CANONICAL_REVISION`: one monotonic counter for committed canonical
  transactions.
- `MEMORY_EVENT`: immutable event rows for create, update, supersede, revoke, and
  delete-shaped operations.
- `FACT_HISTORY_ENTRY`: append-only history preserving previous values before a
  new active value is committed.
- `TOMBSTONE_EVENT`: delete-shaped operations hide active facts without physical
  deletion.
- `SNAPSHOT_MANIFEST`: path-safe manifest with a bounded opaque snapshot ID,
  revision, schema version, creation time, aggregate counts, artifact content
  hash, and complete canonical logical-state hash.
- `RESTORE_REPORT`: sanitized restore result. Restore targets are isolated first
  and require a separate activation gate.
- `INTEGRITY_REPORT`: sanitized fail-closed SQLite, revision, history, and
  destructive-mutation status.

## Recovery Rules

`core.private_memory.CanonicalPrivateMemoryStore` owns canonical fact writes.
Each successful write increments `canonical_revision` exactly once and appends a
matching memory event and fact-history entry in the same SQLite transaction.
Failed transactions roll back without consuming a revision.

Canonical schema creation is limited to the explicit `initialize()` call.
Normal reads, writes, integrity reports, and snapshot creation require an
already-complete canonical schema. They fail closed when required tables,
`private_memory_meta` rows, indexes, triggers, or revision metadata are absent
instead of creating or repairing schema during normal operation.
Required indexes and triggers are validated by their normalized SQL definitions,
not only by object name, so a recreated no-op trigger or wrong-column index is
treated as corrupted schema.

Integrity validation binds canonical rows to their source events: a fact's
`created_at` must match its first event timestamp, a tombstoned fact's
`tombstone_reason` must match the destructive event `reason_code`, and the
canonical revision row's `updated_at` must match the latest committed event
timestamp.
Every distinct `namespace`/`fact_id` represented by canonical events and
history must have exactly one current row in `private_memory_facts`, including
facts hidden by tombstones. A restored delete trigger cannot mask a prior
physical row deletion.

Physical deletion from `private_memory_facts` is blocked by a SQLite trigger.
Delete-shaped operations call `tombstone_fact`, append a tombstone event, and
leave prior history intact.

`core.private_memory_backup.create_snapshot` verifies integrity before copying
the database with SQLite backup and returns only a public-safe manifest. It
rejects an already-existing snapshot ID before copying and publishes the
artifact only through a non-overwriting temporary artifact flow, preserving any
existing bytes unchanged. The manifest never includes local paths or private
values. Caller-provided snapshot IDs are constrained to public-safe opaque
tokens; traversal, separators, empty IDs, and unsafe characters are rejected
before any path is resolved.

`restore_snapshot_to_isolated_target` rejects source/target equality and any
pre-existing target. It validates manifest schema version, canonical revision,
aggregate counts, artifact hash, integrity, and complete canonical state, copies
to a temporary artifact, validates the temporary artifact against the source
snapshot, and publishes only if the target still does not exist. It cleans up
only temporary files created by the current call and reports only sanitized
evidence. A successful isolated restore reports
`activation_required=true`, `activated=false`, and a next action requiring a
separate activation approval. No live runtime activation is implemented by this
API. Failed validation or post-copy integrity checks do not overwrite or delete
live or pre-existing files.

Bulk canonical operations must provide a real pre-operation snapshot proof: the
snapshot artifact plus its manifest. The store verifies the artifact content
hash, schema version, canonical revision, aggregate counts, and complete
canonical logical state against the live database before mutation. The proof
validation runs after acquiring a SQLite write lock and remains in the same
transaction as all bulk mutations, so a stale proof cannot be slipped in between
verification and write. Schema-only, fabricated, stale, corrupted,
provenance-tampered, or different-database proofs fail closed. Bulk writes
validate all inputs before mutation and execute in one SQLite transaction, so a
failed operation leaves revisions, facts, history, and tombstones unchanged.

## Synthetic Evidence

`tests/test_private_memory_recovery.py` covers monotonic revisions, rollback on
failed writes, previous-value history, tombstones, physical delete blocking,
path-safe snapshot manifests, isolated restore smoke without activation,
corrupted snapshot cleanup, existing-target preservation, source/target equality
rejection, manifest tamper rejection, duplicate snapshot ID preservation, bulk
snapshot proof enforcement, stale and different-database proof rejection,
provenance-bound proof rejection, write-lock race coverage, atomic bulk
rollback, traversal-shaped snapshot ID rejection, canonical content tamper
detection, required schema object validation, canonical timestamp/reason
binding, normalized trigger/index definition validation, missing current fact
row detection, explicit-initialize enforcement, and public report redaction.
