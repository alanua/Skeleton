# Graphify Memory Adapter

Skeleton exposes a bounded Graphify adapter only behind `MemoryGateway` and only
for public-safe code relationship diagnostics. Normal `MemoryGateway`
construction does not load Graphify data. The synthetic route is available only
when an adapter is explicitly injected and the caller already has a capability
token for the `skeleton` namespace.

The checked-in fixture is a Graphify `graphify-out/graph.json`-style node-link
graph from the verified Graphify runtime version `0.8.44`. The adapter reads
that fixture directly. It accepts current `links` output and the legacy `edges`
key, resolves edge endpoints through the fixture nodes, and preserves
repository-relative source paths such as `core/memory_gateway.py`.

## Scope

The adapter accepts only:

- namespace `skeleton`
- project id `graphify_synthetic`
- Graphify runtime version `0.8.44`
- query kinds `module_relationship`, `schema_relationship`,
  `test_relationship`, `dependency_relationship`, and
  `provenance_relationship`
- the checked-in Graphify `graph.json` fixture shape

It returns non-authoritative diagnostics with
`authority_classification=derived_code_graph`, source attribution, bounded
repository-relative `source_path` values, `indexed_repo_commit`,
`current_repo_commit`, `indexed_at`, `graphify_runtime_version`,
`graph_schema_version`, and a `stale` flag. Aggregate query reports use
`schemas/graph_memory_query.schema.json` and remain public-safe.

## Public Boundary

The adapter must not return raw Graphify graph objects, node ids, edge ids,
source text, absolute paths, command output, environment values, secrets,
private data, or arbitrary payloads. Deleted fixture relationships are excluded
from results. Malformed fixtures, missing provenance, unverified runtime
versions, private-looking values, path traversal, unsupported query kinds, wrong
scope, and excessive result counts fail closed.

Stale graph evidence may be read as diagnostics, but any `PatchProposal` that
uses stale graph provenance is rejected by `MemoryGateway`.

## Runtime Boundary

This adapter does not run Graphify, scan the filesystem, create or update
canonical storage, start services, use MCP, open network providers, watch files,
install dependencies, activate Hermes, mutate runtime profiles, or write index
state. It is a read-only in-memory adapter over the synthetic fixture.
