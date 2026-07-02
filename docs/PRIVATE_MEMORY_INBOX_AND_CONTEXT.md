# Private Memory Inbox And Context

This document covers the reusable local-private core added for operator-approved memory imports and task context receipts. It is intentionally not wired into Runner.

## Bundle Import

`skeleton-memory import-bundle <basename> --expected-sha256 <hex>` imports one approved JSON bundle from the local inbox into the canonical `PrivateMemoryStack`.

Boundary rules:

- The inbox is `SKELETON_PRIVATE_MEMORY_INBOX`, or `<private-memory-root>/inbox` by default.
- The caller supplies only a safe basename. The importer opens the bundle relative to the verified inbox with no symlink following, then hashes and parses those same opened bytes. Absolute paths, traversal, symlinks, hard-linked files, replacement races, non-regular files, broad modes, oversized files, and SHA-256 mismatches are blocked before parsing or mutation.
- Failed bundles are preserved unchanged.
- Successful bundles are moved atomically into a private `processed` subdirectory under an opaque receipt name.

Atomicity rules:

- The bundle schema, record count, privacy class, operator approval, duplicate canonical refs, safe tokens, and JSON-serializable values are validated before mutation.
- The write uses one stack lock, one pre-operation logical backup, and per-record canonical writes that preserve each record's actor, reason, and approval provenance.
- The import receipt is written under the same stack lock and contains bundle id, bundle hash, record count, provenance refs, canonical refs, and value hashes only.
- MemPalace and Graphify are rebuilt once after the full batch.
- Any canonical write, read-back, or index rebuild failure restores the complete pre-operation SQLite state and rebuilds indexes from that state.
- Re-importing the same `bundle_id` with the same bundle hash is idempotent. Reusing the same `bundle_id` with a different hash is blocked.
- A persistent local backup is created only when `--create-backup` is passed.

## Task Context

`skeleton-memory task-context --project-id ... --task-route ... --profile ... --query ... --namespace ... [--required]` builds a bounded context receipt from canonical SQLite plus MemPalace/Graphify discovery.

Profiles:

- `public_control`: may include executor-visible text only from facts marked `egress_classification: PUBLIC_SAFE_CONTROL` and passing public-safe validation.
- `private_runtime`: returns raw selected values only in the in-process `TaskMemoryContextResult.private_values`; CLI and public receipts remain aggregate-only.
- `none`: returns an empty receipt.

Context rules:

- Loading is read-only and never mutates canonical memory.
- Derived index hits are only candidates. Every selected record is confirmed by exact canonical SQLite read before inclusion.
- Task context is restricted to the explicit `skeleton.context` namespace policy; unsupported requested namespaces fail closed.
- Required context blocks unless the stack is `READY`; optional context returns an unavailable bounded receipt.
- Selection is capped at 10 records and 6000 rendered characters. For `private_runtime`, the cap applies to the actual returned private values by canonical JSON size; oversized values are not returned. Truncation is deterministic and recorded.
- Public receipts contain canonical revision, selected refs, value hashes, counts, limits, context hash, profile, and status. They must not contain paths or raw private values.
