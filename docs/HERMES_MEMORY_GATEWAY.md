# Hermes Memory Gateway

Hermes memory access is review-only and gateway-only. Hermes does not open SQLite,
Graphify, MemPalace, registry files, local files, or direct storage APIs.

## Binding

Every Hermes memory task is bound to exactly one `namespace` and one `project_id`.
They are distinct bounded identifiers:

- `namespace` must be one of the Memory Gateway namespaces.
- `project_id` is not restricted to the namespace enum and does not need to equal
  `namespace`.
- A task or proposal for a different `project_id` in the same namespace fails
  closed as `PROJECT_NOT_AUTHORIZED`.

## Entrypoint

Hermes submits `hermes.memory_task_packet.v1` packets to
`run_hermes_memory_task_packet`. The worker validates public-safe flags, mutation
boundaries, approval requirements, and the allowlisted operation before creating
`HermesMemoryAdapter`.

The adapter then builds a `skeleton.hermes_memory_request.v1` request and calls
`MemoryGateway.execute`. It has no storage-specific read or write path.

Hermes exposes exactly these memory operations:

- `memory.lookup_exact`
- `memory.get_conflicts`
- `memory.get_override_history`
- `memory.get_audit_log`
- `memory.get_index_freshness`
- `memory.propose_patch`

The broader Memory Gateway still preserves semantic search, graph query, and
separate graph freshness commands for the Gateway contract. Those commands are
not Hermes adapter operations.

## Isolation

Canonical exact reads, conflicts, override history, audit events, freshness
metadata, and patch proposals are filtered by the explicit project scope.
Projects sharing a namespace, for example `aufmass/project-a` and
`aufmass/project-b`, do not share project-scoped records or events.

## Idempotency

`MemoryPatchProposalRegistry.lookup_by_idempotency_key(...)` is the bounded
public lookup API for exact proposal idempotency. Callers must not inspect
underscore/private registry fields.

`DUPLICATE_EXISTING` is classified only when the exact idempotency key already
exists. The same dedupe target with a changed payload remains `REVIEW_REQUIRED`.

## Writes

Canonical writes remain disabled for Hermes. `memory.propose_patch` only records a
patch proposal through the gateway and returns `OPERATOR_APPROVAL_REQUIRED` for a
new proposal. Repeating the same proposal against reused gateway/registry state
returns `DUPLICATE_EXISTING`.

Operator approval remains mandatory for canonical promotion outside this adapter.
Proposal events keep `canonical_write_performed=false`.
