# Audit Ledger

`core/audit_ledger.py` provides the stage 1 JSONL audit trail for Skeleton
operational events. SQLite stores current structured state; JSONL records the
append-only audit trail of what happened.

`AuditLedger.append(event)` validates a public-safe event, adds an id,
schema, and UTC ISO8601 timestamp when missing, then appends one compact JSON
object to the configured file. It does not delete or overwrite existing
entries.

`AuditLedger.read_recent(n)` returns the newest `n` JSONL entries from the
current ledger file. `rotate_if_needed(max_size_mb=50)` renames an oversized
ledger to a timestamped `.rotated` file and creates a fresh empty ledger. The
old file is preserved.

## Safety Rules

Audit events reject obvious secret fields and values, including API keys,
tokens, passwords, private keys, and `.env`-style content. They also reject
Drive URLs or file-id fields, raw private filesystem paths, and long raw log
strings.

`private_reference_stub` events are allowed only as opaque references. They may
carry a stub id, reference type, and label, but not raw private content,
locators, Drive file ids, URLs, or paths.

GitHub is still only for public-safe canon and handoff. Secrets remain in the
protected runtime environment or a secrets manager, and drawings or private
files remain in private Drive or a controlled private-data workdir.
