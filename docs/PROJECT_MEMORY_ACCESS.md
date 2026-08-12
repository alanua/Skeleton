# Project Memory Access

`core/project_memory_adapter.py` is the universal project-to-`MemoryGateway`
adapter. It preserves the six project memory classes:

- `CANON`
- `REVIEW`
- `BACKLOG`
- `REJECTED`
- `PRIVATE`
- `TEMPORARY`

The adapter supports `read_context`, `propose_fact`, `propose_task`,
`propose_preference`, and `list_pending_review`.

`CANON` is proposal/review controlled. Canonical proposals are submitted through
`MemoryGateway.memory.propose_patch` and the adapter receipt marks
`canonical_write_performed` as `false` with explicit operator approval required.
The adapter does not import canonical manifests or create another canonical
database.

`PRIVATE` writes are allowed only for synthetic/private-runtime callers when all
of these are true:

- the request uses `memory_class: PRIVATE`
- the request uses `capability_class: PRIVATE_RUNTIME_ONLY`
- the adapter binding uses `PRIVATE_RUNTIME_ONLY`
- the injected `MemoryGateway` token is private runtime capable

When those checks pass, the adapter routes mutation through
`MemoryGateway.memory.private_mutate` and exact readback through
`MemoryGateway.memory.private_read_exact`. Public receipts are projected through
the existing gateway public-payload redaction helpers, so raw private values are
not exposed in mutation receipts.
