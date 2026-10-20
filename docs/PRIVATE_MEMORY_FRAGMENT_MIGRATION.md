# Private Memory Fragment Migration

This runbook covers the local-private audit and migration path for fragmented
Skeleton knowledge sources.

The scanner writes exact source inventory only to the local private runtime. Public
output is limited to aggregate counts, hashes, revision numbers, backup status,
validation status, and opaque conflict counts.

## Commands

Dry run:

```bash
python3 scripts/private_memory_fragment_migration.py --repo-root . --include-private-roots
```

Apply after reviewing the local-private dry-run inventory:

```bash
python3 scripts/private_memory_fragment_migration.py --repo-root . --include-private-roots --apply
```

For isolated validation or synthetic fixtures, pass a private root explicitly:

```bash
python3 scripts/private_memory_fragment_migration.py --repo-root tests/fixtures --private-root /tmp/skeleton-private-memory --apply
```

## Policy

Only durable fact candidates are migrated. Raw documents, scans, media, full
transcripts, credentials, secret-like files, high-volume artifacts, raw telemetry,
unsupported namespaces, and ambiguous conflicts are excluded or marked for
operator review.

Every canonical mutation is sent through `skeleton.memory.private_mutate` using
`MemoryGateway` and `PrivateMemoryGatewayStorage`. The migration code does not
directly write canonical SQLite facts or events.

Before the first mutation, the canonical database is backed up with the existing
private stack backup path and the migration ledger state is hashed. The migration
ledger records deterministic idempotency keys so reruns skip already-applied
candidates and do not advance the canonical revision.

Derived MemPalace and Graphify rebuilds are best effort. If a derived rebuild
fails after a canonical commit, the canonical write is preserved and the public
receipt reports degraded indexes without exposing private source data.

## Public Report Template

The public-safe receipt must include only:

- aggregate classification counts;
- migrated, duplicate, skipped, conflict, and needs-operator counts;
- plan and inventory hashes;
- canonical revision before and after;
- backup confirmation and aggregate backup counts;
- canonical integrity status;
- validation command results.

Do not include real source paths, customer names, document names, device values,
chat payloads, credentials, or local private root paths.
