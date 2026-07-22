# Memory Gateway

The Memory Gateway is a bounded synthetic contract for namespace-scoped memory and code-graph access. It does not install Graphify or MemPalace and does not expose storage-specific APIs to Hermes.

## Contract

- Requests use `skeleton.memory_gateway.request.v1`.
- Responses use `skeleton.memory_gateway.response.v1`.
- Supported namespaces are `aufmass`, `bauclock`, `skeleton`, `home_automation`, and `legal_private`.
- Namespace is mandatory, must match the command prefix, and must be authorized by the capability token.
- Wildcard namespace access is rejected by default.
- Public mode rejects private namespaces and private-looking values.

Allowlisted commands are namespace-qualified:

- `<namespace>.memory.lookup_exact`
- `<namespace>.memory.search_semantic`
- `<namespace>.memory.get_conflicts`
- `<namespace>.memory.get_override_history`
- `<namespace>.memory.get_audit_log`
- `<namespace>.memory.get_index_freshness`
- `<namespace>.memory.prepare_canonical_manifest`
- `<namespace>.memory.import_canonical_manifest`
- `<namespace>.memory.private_mutate`
- `<namespace>.graph.query_code`
- `<namespace>.graph.get_index_freshness`
- `<namespace>.memory.propose_patch`

## Authority

`memory.lookup_exact` returns canonical facts with `authoritative=true`, `canonical_ref`, `canonical_revision`, provenance refs, source kind `canonical_sqlite`, authority classification `canonical_exact`, and namespace.

`memory.search_semantic` always returns `authoritative=false`, source kind `mempalace`, and authority classification `derived_semantic`.

`graph.query_code` always returns `authoritative=false` for canonical facts, `authoritative_scope=code_graph`, source kind `graphify`, and authority classification `derived_code_graph`.

## Freshness

Graphify freshness includes `indexed_repo_commit`, `current_repo_commit`, `indexed_at`, `stale`, and `index_namespace`.

MemPalace freshness includes `indexed_canonical_revision`, `current_canonical_revision`, `source_snapshot_id`, `indexed_at`, `stale`, and `index_namespace`.

Canonical SQLite freshness includes `current_canonical_revision`.

Stale derived results may be displayed with a warning but cannot support `memory.propose_patch` until refreshed and exact-confirmed.

## Writes

Public writes are only accepted through `memory.propose_patch`, which delegates to `MemoryPatchProposalRegistry`. Semantic or graph evidence must be exact-confirmed with `confirmed_via_exact_ref` and `confirmed_canonical_revision`.

The local `skeleton-memory` CLI has one private compatibility boundary, `skeleton.memory.private_mutate`, for operator-approved `put`, `delete`, and `import_bundle` mutations. It requires a non-public `skeleton` capability token and an injected private storage adapter. Requests carry project scope, approval, actor, reason, expected revision, idempotency key, source hash, and bundle hash metadata where applicable. The adapter records the mutation before entering the stack and recovers by transaction reference, so retry after a canonical SQLite commit cannot advance the canonical revision a second time. Receipts are sanitized and omit raw private values, paths, SQLite connection details, and bundle contents.

Stable rejection reasons include:

- `SEMANTIC_RESULT_NOT_CANON_CONFIRMED`
- `GRAPH_RESULT_NOT_CANON_CONFIRMED`
- `STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE`
- `EXACT_CONFIRMATION_REVISION_MISMATCH`

`memory.get_conflicts` returns unresolved source/value conflicts from patch proposals only. `memory.get_override_history` returns the separate ordered override lifecycle. `memory.get_audit_log` returns sanitized actor, reason, approval, and revision metadata only.
