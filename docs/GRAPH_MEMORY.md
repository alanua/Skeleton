# Graph Memory

Skeleton may adopt Graphify as a private derived graph-memory layer for local
orientation across projects. This is an architecture specification only. It does
not install Graphify, run Graphify, add a runtime service, ingest private files,
or publish graph output.

Graphify is not a source of truth for Skeleton. It is a private local index built
from already-approved local memory records and repository metadata so an operator
can ask relationship questions without treating model inference or graph edges
as canonical facts.

## Authority Ladder

When memory sources disagree, Skeleton resolves decisions in this order:

1. Human approval and explicit operator decisions.
2. Current GitHub state, including the checked-in repository content and issue or
   pull request state when relevant.
3. Protected repository rules, governance contracts, manifests, schemas, and
   deployment boundaries.
4. SQLite canonical project memory held by the private memory connector.
5. Graphify derived graph memory built from approved local inputs.
6. LLM inference, summaries, and recommendations.

Graphify output may help find related records, stale assumptions, dependency
clusters, or questions for review. It may not override the higher layers, create
canon records on its own, approve work, change repository policy, or route live
runtime behavior.

## Private Derived Layer

The graph-memory layer is local/private by default:

- Inputs come only from approved local sources that are already allowed to feed
  private project memory.
- The graph stores derived nodes, edges, embeddings, labels, and summaries
  locally.
- The graph may be rebuilt from canonical inputs; Graphify data is disposable
  cache/index state, not durable authority.
- Every graph answer must retain provenance back to a higher-authority local
  record or repository fact before it can influence operator decisions.
- Unknown, stale, contradictory, or weakly inferred relationships are review
  signals, not facts.

The public repository may contain architecture docs, schemas, connector boundary
contracts, and synthetic examples only. It must not contain real graph exports,
private graph databases, Graphify outputs, local paths, secrets, Drive IDs,
Telegram IDs, customer data, task payloads, Aufmass quantities, or private
project records.

## Public And Private Boundary

Public-safe graph outputs are limited to aggregate or synthetic information:

- Schema names and contract versions.
- Query status values such as `DONE` or `BLOCKED`.
- Synthetic query IDs and synthetic project references.
- Allowlisted query kinds.
- Aggregate counts by node, edge, attention, blocked, stale, or provenance state.
- Error class names and allowlisted next-action tokens.
- Synthetic fixtures created only for tests or documentation.

Private graph outputs stay local and must not be committed or pasted into public
issues:

- Node IDs, edge IDs, labels, aliases, summaries, chunks, embeddings, scores, and
  traversal paths from real project memory.
- Local filesystem paths, registry values, database names, table names, SQL, raw
  Graphify payloads, provider outputs, or environment values.
- Real project names, customer names, Drive identifiers, Telegram identifiers,
  task titles, Aufmass quantities, measurements, room names, and source records.

Any bridge that cannot prove a report is public-safe must fail closed with
`BLOCKED` status and emit only an error class and next-action token.

## Query Contract

`schemas/graph_memory_query.schema.json` documents the public-safe envelope for
future graph-memory query requests and aggregate reports. It is intentionally
synthetic:

- Requests identify only a synthetic query and project reference.
- Query kinds are allowlisted orientation questions.
- Filters are limited to public-safe booleans and status tokens.
- Reports return aggregate counts and never return graph paths or record
  excerpts.
- Unsafe, unsupported, or over-broad requests resolve to `BLOCKED`.

This contract is suitable for tests and public planning. A future private
adapter may translate local operator questions into private Graphify calls, but
that adapter must redact outputs before anything crosses into GitHub-visible
logs, issues, pull requests, or committed files.

## Relationship To Private Memory

`docs/PRIVATE_MEMORY.md` defines the SQLite private memory boundary. SQLite
canonical project memory remains the durable local memory source. Graphify is a
derived graph index over that source and over approved public repository facts.

`docs/PROJECT_MEMORY_REGISTRY.md` defines public-safe cross-project aggregate
status. Graph memory may help the local operator understand relationships across
projects, but public registry summaries remain aggregate-only and must not expose
real project graph details.

## Pilot Plan

The first pilot should graph only the public-safe Skeleton repository structure,
not private project memory:

1. Define synthetic graph fixtures from public docs, schemas, and module names.
2. Validate the public query schema with aggregate-only request and response
   examples.
3. Run a local-only dry design review that maps possible graph nodes such as
   document, schema, module, contract, and governance rule.
4. Confirm that every public report contains only synthetic references,
   aggregate counts, status, error class, and next-action token.
5. Separately, design the private adapter contract for real local memory, with
   no Graphify installation or ingestion in the public repo task.
6. Require operator approval before any later private pilot ingests local
   records.

The pilot is complete only when the public repo can explain the architecture and
validate synthetic query envelopes without exposing real private graph memory.

## Runtime Installation Boundary

The Runner may install Graphify only through the approval-gated
`install_graphify_runtime` maintenance task. That task pins the tool with
`uv tool install --reinstall graphifyy==0.8.44`, verifies the local CLI contract,
backs up only bounded Graphify-managed Codex and Hermes skill paths plus
existing marker-only `.graphify_version` files discovered from the pinned
Graphify 0.8.44 upstream platform destination allowlist, and installs skills
with `graphify install --platform codex` and `graphify install --platform
hermes`. The allowlist is exact and does not scan arbitrary home directories.

The local smoke check is synthetic and AST-only in scope. It uses the supported
Graphify 0.8.44 build form, `graphify <folder>`, with `GRAPHIFY_OUT` set to a
temporary output directory and a bounded timeout. The smoke is successful only
when `graph.json` exists and reports non-zero node and edge counts. The smoke
environment is scrubbed of model credentials and keeps network access, hooks,
services, ports, and private indexing disabled.

Unsupported command shapes are outside the contract: `graphify ingest`,
`graphify install-skills`, `--source`, `--extractor`, and `--no-semantic` must
not appear in Runner runtime commands or tests. If a post-backup skill install,
smoke step, or other unexpected runtime failure occurs, the Runner restores the
bounded Codex and Hermes skill paths and allowlisted `.graphify_version` files
from the private recovery snapshot. A successful runtime install retains the
private recovery snapshot for operator recovery. Public reports remain
aggregate-only and must not include paths, Graphify command output, environment
values, profile content, node IDs, edge IDs, labels, summaries, or graph
payloads.

Runtime/server/service integration remains blocked by issue #1047.
