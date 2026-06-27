# Hermes Memory Gateway

Hermes memory access is gateway-only and bound to one explicit `(namespace, project_id)` pair. The adapter does not expose storage internals, does not write canonical memory, and does not bypass operator approval metadata.

## Scope

- Adapter module: `core/hermes_memory_adapter.py`
- Request schema: `skeleton.hermes_memory.request.v1`
- Result schema: `skeleton.hermes_memory.result.v1`
- Required packet scope: `namespace` plus `project_id`
- Allowed Hermes capabilities: `lookup_exact`, `get_conflicts`, `get_override_history`, `get_audit_log`, `get_index_freshness`, and `propose_patch`

The broader Memory Gateway still preserves semantic search, graph query, and separate freshness commands for the #1124 contract. Those commands are not added to the Hermes adapter capability scope.

## Isolation

Canonical exact reads, conflicts, override history, audit events, freshness metadata, and patch proposals are filtered by the explicit project scope. Projects sharing a namespace, for example `aufmass/project-a` and `aufmass/project-b`, must not share project-scoped records or events.

## Idempotency

`MemoryPatchProposalRegistry.lookup_by_idempotency_key(...)` is the bounded public lookup API for exact proposal idempotency. Callers must not inspect underscore/private registry fields.

`DUPLICATE_EXISTING` is classified only when the exact canonical idempotency key already exists. The same dedupe target with a changed payload or changed idempotency key remains `REVIEW_REQUIRED`.

## Write Boundary

`propose_patch` remains a proposal route only. It requires exact canonical confirmation and operator approval metadata, and it does not promote or mutate canonical records.
