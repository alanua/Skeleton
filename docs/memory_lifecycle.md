# Skeleton Memory Lifecycle

This document is public-safe: it describes schemas, boundaries, and synthetic call-site inventory only. Real Life Archive, Home Edge, document, device, customer, operator, and local path values remain local-private.

## Architecture

`MemoryGateway` is the only normal mutation boundary for Skeleton memory. Normal domain code invokes typed gateway commands; only gateway storage adapters and canonical storage internals may touch `PrivateMemoryStack`, `CanonicalPrivateMemoryStore`, or SQLite mutation APIs directly.

The reusable lifecycle in `core.memory_lifecycle` has two phases:

1. `recall_before_task(...)` resolves operator, domain, project, dataset, namespace, and privacy scope, then retrieves bounded context through `skeleton.memory.private_status`, `skeleton.memory.private_search_semantic`, `skeleton.graph.private_query`, and exact `skeleton.memory.private_read_exact` confirmation.
2. `capture_after_event(...)` classifies durable candidates after meaningful input, confirmed decisions, observations, artifact ingestion, device events, completed actions, or explicit manual “remember this”, then writes accepted candidates through `skeleton.memory.private_mutate`.

Canonical commits remain in the existing private SQLite authority. MemPalace and Graphify are derived layers only. A successful canonical commit is not rolled back when a derived rebuild fails; the typed result reports `DEGRADED` and includes aggregate degraded-index names.

Public receipts include refs, revisions, hashes, status, counts, and degradation flags only. Private typed results may include private values for local runtime use; public receipts must not contain source payloads, private values, secrets, raw artifacts, or local paths.

## Domain Scopes

The lifecycle currently registers these explicit domains:

- `skeleton` and `core` -> `skeleton.context`
- `life_archive` -> `life_archive.context`
- `home_edge` and `home_devices` -> `home_edge.devices`
- `documents` and `mfp` -> `documents.metadata`
- `travel` -> `travel.context`
- `dios` -> `dios.aufmass`
- `aufmass` -> `aufmass.context`
- `gewerbe` and `bauclock` -> `gewerbe.bauclock`
- `runner` -> `runner.context`

Unknown domains, privacy classes, schemas, capability modes, and unapproved secret or raw telemetry classes fail closed.

## Capture Policy

Automatic capture accepts durable preferences, identity/context facts, confirmed decisions, configurations, relationships, project state, document metadata, device state changes worth retaining, and completed-action outcomes.

Automatic capture skips transient chatter, low-confidence candidates, unsupported event types, unsupported classifications, and missing payloads. It rejects secrets or raw high-volume telemetry unless a future authorized secret boundary is explicitly added.

Corrections and supersessions are modeled as new revisions for the same fact or with a `supersedes` canonical ref in the canonical value. Idempotency keys replay identical accepted mutations without advancing the canonical revision again.

Manual “remember this” remains a force-capture override only for otherwise durable classifications; it does not bypass namespace, privacy, schema, provenance, approval, idempotency, or gateway boundaries.

## Mutation Call-Site Inventory

Normal application/domain mutation paths:

- `core.memory_lifecycle.capture_after_event` -> `MemoryGateway.execute` -> `skeleton.memory.private_mutate`
- `core.aufmass_memory_bridge.AufmassMemoryBridge.write_calculation` -> internal gateway compatibility wrapper -> `skeleton.memory.private_mutate`
- `core.aufmass_memory_bridge.AufmassMemoryBridge.write_review_decision` -> internal gateway compatibility wrapper -> `skeleton.memory.private_mutate`
- existing CLI/runtime private-memory compatibility paths documented in `docs/PRIVATE_MEMORY_STACK.md` -> `skeleton.memory.private_mutate`

Allowed internal authority/storage paths:

- `core.memory_gateway_storage.PrivateMemoryGatewayStorage` calls `PrivateMemoryStack.put`, `delete`, and `import_bundle` after validating typed gateway mutation payloads.
- `core.private_memory_stack.PrivateMemoryStack` calls `CanonicalPrivateMemoryStore` and rebuilds derived indexes best-effort after canonical success.
- `core.private_memory.CanonicalPrivateMemoryStore` owns canonical SQLite revision, provenance, dedupe/idempotency history, and integrity semantics.
- tests may instantiate lower layers directly for parity, recovery, and integrity checks.

No domain owns a competing canonical memory database. Other SQLite users in `core/` are unrelated operational stores, such as runner leases or loop state, not canonical private memory.
