# Hermes Memory Gateway

Hermes memory access is bounded to the namespaced Memory Gateway through
`core.hermes_memory_adapter.HermesMemoryAdapter`.

The Hermes packet binds exactly one `namespace` and matching `project_id`.
Hermes may call only these public-safe capabilities:

- `memory.lookup_exact`
- `memory.get_conflicts`
- `memory.get_override_history`
- `memory.get_audit_log`
- `memory.get_index_freshness`
- `memory.propose_patch`

Hermes must not call SQLite, Graphify, MemPalace, filesystem paths, or local
registries directly. Derived search and graph commands remain gateway internals
and are not exposed to Hermes.

Exact canonical reads are authoritative and include `canonical_revision` and
sanitized provenance refs. Patch proposals are allowed only as non-destructive
proposal events with deterministic `dedupe_key` and `idempotency_key`, exact
canonical confirmation, provenance, and approval metadata. The adapter never
commits canonical memory.

Write-gate outcomes are limited to:

- `APPROVED_FOR_OPERATOR`
- `REVIEW_REQUIRED`
- `BLOCKED`
- `DUPLICATE_EXISTING`

Model confidence, task completion, or proposal generation never implies
approval. Override proposals require explicit `override_intent: true` and the
separate `override_operator` approval tier.

Public reports are aggregate and sanitized. The synthetic Aufmass scenario in
`run_hermes_aufmass_memory_gateway_scenario()` proves:

1. Hermes reads an accepted public synthetic room rule.
2. Hermes inspects conflicts and override history separately.
3. Hermes checks freshness.
4. Hermes proposes a deterministic non-destructive patch.
5. The gate requires operator review.
6. The repeated proposal is idempotent.
7. No canonical write is performed.
