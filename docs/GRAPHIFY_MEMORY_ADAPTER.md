# Graphify Memory Adapter

Skeleton exposes a bounded Graphify adapter only behind `MemoryGateway` and only
for synthetic public-safe code relationships. Normal `MemoryGateway`
construction does not load Graphify data. The synthetic route is available only
when an adapter is explicitly injected and the caller already has a capability
token for the `skeleton` namespace.

## Scope

The adapter accepts only:

- namespace `skeleton`
- project id `graphify_synthetic`
- index namespace `skeleton`
- runtime profile `graphify-hermes-readonly-0.8.44`
- runtime version `0.8.44`
- query kinds `module_relationship`, `schema_relationship`,
  `test_relationship`, `dependency_relationship`, and
  `provenance_relationship`
- the checked-in synthetic fixture shape

It returns non-authoritative diagnostics with
`authoritative=false`, `authoritative_scope=code_graph`,
`authority_classification=derived_code_graph`, `namespace`, `query_ref`,
`result_refs`, sanitized `source_paths_public_safe`, source attribution,
`indexed_repo_commit`, `current_repo_commit`, `indexed_at`,
`graph_schema_version`, and a `stale` flag. Aggregate query reports use
`schemas/graph_memory_query.schema.json` and remain public-safe.

## Public Boundary

The adapter must not return raw Graphify graph objects, node ids, edge ids,
source text, paths, command output, environment values, secrets, private data,
or arbitrary payloads. Deleted fixture relationships are excluded from results.
Malformed fixtures, missing provenance, private-looking values, unsupported
query kinds, wrong scope, and excessive result counts fail closed.

Hermes cannot pass Graphify CLI arguments, paths, environment overrides,
commands, write, reindex, watch, hook, MCP, service, or port options through
this adapter. The only query option is a boolean `include_stale`, and stale
results remain diagnostic only.

Stale graph evidence may be read as diagnostics, but any `PatchProposal` that
uses stale graph provenance is rejected by `MemoryGateway`.

## Runtime Boundary

This adapter does not run Graphify, scan the filesystem, create or update
canonical storage, start services, use MCP, open network providers, watch files,
install dependencies, activate Hermes, mutate runtime profiles, or write index
state. It resolves Graphify only by validating approved pinned runtime metadata
already present in the synthetic fixture. If runtime availability, runtime
version, runtime profile, index namespace, graph schema version, or privacy
checks fail, construction fails closed before any query response is available.
