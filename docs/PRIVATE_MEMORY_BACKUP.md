# Private Memory Backup

Private memory backup is a local-only integrity contract for the canonical `PrivateMemoryStack` SQLite authority. It does not create a second canonical store and does not bypass `MemoryGateway` for normal writes.

## Snapshot Contract

Each backup writes:

- `backups/<snapshot-id>.sqlite`: SQLite backup made through SQLite's backup API
- `backups/<snapshot-id>.manifest.json`: bounded manifest for the snapshot

The manifest binds:

- manifest schema and local backup creator
- snapshot id and creation timestamp
- canonical SQLite schema version
- canonical revision
- aggregate fact/event/history/tombstone counts
- bounded file size
- hash class (`sha256`) and local content hash
- SQLite schema fingerprint
- canonical logical state hash

The manifest is local recovery metadata. Public receipts should expose only status, snapshot id, revision, hash class, aggregate counts, and sanitized error class.

## Verification

Verification fails closed for:

- missing, corrupt, or truncated snapshot files
- manifest/schema/hash/count/revision mismatches
- snapshots outside the bounded file hash contract
- foreign or incompatible SQLite schema
- canonical history or SQLite integrity failures

Verification compares the snapshot revision with the current canonical revision. Older snapshots are reported as `STALE`; this classification blocks dry-run restore from being treated as a successful replacement candidate.

## Dry-Run Restore

Dry-run restore copies the snapshot into an isolated temporary target, verifies integrity, checks logical readback, and deletes the temporary file. It never mutates or replaces `canonical.sqlite`.

A successful dry-run reports `activation_required=true` and `activated=false`. Any real restore must be a separate later operator/runtime action, outside code generation. After a real restore, MemPalace, Graphify, and Cognee projections are rebuildable derived state and must be rebuilt from canonical SQLite before use.
