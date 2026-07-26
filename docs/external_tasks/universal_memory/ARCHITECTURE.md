# Binding architecture

The general five-layer private-memory target is:

```text
canonical SQLite
→ private MemoryGateway policy boundary
→ Cognee primary semantic projection, local-only and revision-fresh
→ MemPalace explicit semantic fallback
→ Graphify independent deterministic graph
→ mandatory project-scoped bootstrap
→ 0600 local-private context handoff
```

Binding invariants:

- SQLite is the sole canonical authority for this general memory stack.
- MemoryGateway is the only normal mutation boundary.
- Cognee, MemPalace, and Graphify are derived, non-authoritative, rebuildable projections.
- Projection failure must never erase or roll back an already committed canonical mutation.
- Retrieval is project/dataset/namespace scoped and revision aware.
- Everything material is deterministic, idempotent, auditable, and replayable.
- No second canonical database may be introduced for this stack.
- No direct SQLite mutation around MemoryGateway.
- No memory-free fallback when mandatory memory is configured.
- Runtime dependencies are local-only; non-loopback providers and silent cloud fallbacks fail closed.
- Kimi is proposal-only: no merge, deploy, production activation, canon approval, or secret access.

Scope boundary:

Issue #1904 is the general Universal Memory runtime. It is not the authority for Gewerbe/business accounting data. Do not modify, absorb, or close #1958.
