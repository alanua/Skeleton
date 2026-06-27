# Project Memory Registry

The project memory registry is a local-only orientation layer for Skeleton. Its
purpose is to summarize whether local project memory is configured, usable, and
needs operator attention across projects without publishing project records or
private content.

This repository may contain only the registry contract, schema, connector code,
and synthetic tests. Real project memory files, local project names, local paths,
task payloads, provider outputs, Drive identifiers, secrets, and raw project
records stay on the server.

## Public Boundary

Public reports may contain aggregate fields only:

- Registry schema and status.
- Total project count.
- Counts by public-safe project state.
- Counts by public-safe attention state.
- Counts for schema-ready, stale, and blocked projects.
- Aggregate backlog and open-decision counts.
- Error class names and next-action tokens.

Public reports must not contain project IDs, project names, paths, repository
URLs, branch names, raw task titles, memory excerpts, SQL, table names, tokens,
secrets, provider outputs, or environment values.

Any invalid input, unsafe marker, unsupported schema, or unsafe report value
fails closed with `BLOCKED` status.

## Local Registry Input

The public foundation accepts only synthetic aggregate project status records in
tests. The record shape is documented by
`schemas/private_project_memory.schema.json` as
`skeleton.private_project_memory.project_status.v0`.

Each local record describes a single project using public-safe status fields:

- `project_ref`: synthetic public-safe reference only.
- `state`: one of `active`, `paused`, `blocked`, `archived`, or `unknown`.
- `attention`: one of `none`, `review`, `operator`, `blocked`, or `unknown`.
- `schema_ready`: whether the local project memory schema is ready.
- `stale`: whether local memory needs refresh.
- `task_backlog_count`: aggregate task count only.
- `open_decision_count`: aggregate decision count only.

The current implementation does not ingest real files. Callers pass in-memory
records that have already been reduced locally to public-safe fields.

Runtime validation enforces this exact status-record key allowlist before any
aggregation occurs:

- `schema`
- `project_ref`
- `state`
- `attention`
- `schema_ready`
- `stale`
- `task_backlog_count`
- `open_decision_count`

Any other status-record key, including write-shaped markers such as `operation`,
`action`, `actor`, or `neutral_unknown`, fails closed before aggregation.

## Aggregate Report

The aggregate report uses schema
`skeleton.private_project_memory.registry_summary.v0` and includes no per-project
records. A successful report has `DONE` status. A failed report has `BLOCKED`
status and zeroed counts.

`next_operator_action` is an allowlisted token:

- `none`
- `configure_project_memory_registry`
- `review_blocked_project_memory`
- `refresh_stale_project_memory`
- `review_project_memory_attention`
- `initialize_project_memory_schema`

## Relationship To Private Memory

`core/private_memory.py` proves the SQLite connector boundary for a single local
private memory database. `core/private_project_memory.py` adds the cross-project
aggregate registry foundation. Both are intentionally public-safe foundations:
they can report aggregate status, but they do not expose real memory content or
wire live runtime services.

## Canonical Patch Proposals

Canonical project memory writes must enter through an explicit patch-proposal
contract before any canonical fact is accepted. The proposal shape is documented
by `schemas/memory_patch_proposal.schema.json` and implemented in
`core/memory_patch_proposal.py`.

Every proposal must carry namespace-scoped identity fields, exact evidence,
operator approval metadata, and the canonical revision observed before the
write:

- `namespace`
- `project_id`
- `object_id`
- `entity_scope`
- `fact_type`
- `normalized_target`
- `source_evidence_hash`
- `dedupe_key`
- `idempotency_key`
- `proposed_value`
- `provenance_refs`
- `actor_ref`
- `reason_code`
- `approval_tier`
- `confirmed_via_exact_ref`
- `confirmed_canonical_revision`

The dedupe key is never trusted from caller text. The server recomputes it from
`namespace`, `project_id`, `object_id`, `entity_scope`, `fact_type`,
`normalized_target`, and `source_evidence_hash`, then fails closed if the caller
key differs or is malformed. Idempotency keys must be explicit and well-formed;
reusing an idempotency key with a different payload is rejected.

Exact duplicate proposals return the existing accepted event and canonical ref.
The same target with different evidence or value becomes `REVIEW_REQUIRED`.
Semantic-only evidence cannot authorize a canonical write. Accepted events record
the explicit approval ref and the confirmed canonical revision.

## As-Built Overrides

Operator overrides are not ordinary patch conflicts. They use the separate event
family documented by `schemas/memory_override_event.schema.json` and implemented
in `core/memory_override.py`:

- `OVERRIDE_PROPOSAL`
- `OVERRIDE_APPROVAL`
- `OVERRIDE_ACTIVATION`
- `OVERRIDE_SUPERSESSION`
- `OVERRIDE_REVOCATION`

An active fact can point at an approved override activation event while the
previous canonical value and canonical ref remain intact in the audit chain.
Override activation requires explicit operator approval and exact evidence refs.
Approved override history is returned by `get_override_history`; unresolved
ordinary proposal conflicts are returned separately by `get_conflicts`.

## Relationship To Graph Memory

`docs/GRAPH_MEMORY.md` defines the planned Graphify layer as private derived
graph memory. It may index relationships across locally approved project memory
records, but the registry remains public-safe aggregate orientation only.

The authority order is human approval, current GitHub state, protected repo
rules, SQLite canonical project memory, Graphify derived graph memory, then LLM
inference. Registry summaries must not include Graphify nodes, edges, paths,
labels, embeddings, scores, raw project records, local paths, customer data, or
Aufmass quantities.

## Current State

This is a foundation only. It does not run Aufmass, ingest real files, modify
runtime services, route providers, or publish private project records.
