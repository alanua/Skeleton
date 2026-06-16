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

Write-shaped inputs also fail closed before aggregation. The public foundation
does not accept append, insert, update, upsert, patch, commit, destination, or
registry-record shaped payloads, even when the surrounding values are synthetic.
Those inputs are reserved for a later private-only storage stage and must not
become either a public summary or a local registry record through this module.

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

## Current State

This is a foundation only. It does not run Aufmass, ingest real files, modify
runtime services, route providers, or publish private project records.

A full private project memory store is a later stage. This stage proves only the
public-safe aggregate contract and the fail-closed boundary around unsafe
write-shaped input.
