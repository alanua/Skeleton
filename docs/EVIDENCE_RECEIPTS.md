# Evidence Receipts

`core.public_receipt` provides the reusable public-receipt boundary for nested
Skeleton receipts. Callers must pass an explicit allowlist of `PublicField`
paths; the renderer does not dump arbitrary receipt dictionaries.

The boundary scans source receipts before rendering. Secret-marked fields,
credential fields, raw provider identifiers, private paths, private-key
sentinels, token-like values, and non-JSON values fail closed. Public hashes,
aggregate counts, status codes, booleans, non-negative integers, strings, and
opaque public ids remain usable only when their exact field path is allowlisted.

Nested mappings and lists are rendered deterministically. Mapping keys are
sorted, list order is preserved, and running the sanitizer on its own output
with the same allowlist is idempotent.

Known private evidence should stay outside public receipts. When a receipt must
acknowledge private evidence, publish only an opaque private evidence reference
or a bounded `private_summary(...)` class/count object. `AuditLedger` keeps the
existing append-only JSONL convention and exposes `append_public_receipt(...)`
for storing sanitized receipt metadata alongside an opaque private evidence
reference without adding a second ledger.
