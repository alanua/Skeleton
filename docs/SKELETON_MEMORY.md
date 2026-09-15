# Skeleton Memory

Skeleton operational memory is server-side state for the control layer. It is
not Jeeves memory, private document storage, or the canonical private memory
write path.

## Authority and continuity boundary

`SkeletonMemory` and the canonical `MemoryGateway` stack are distinct.

- `MemoryGateway` backed by canonical private SQLite is the durable authority for approved private facts, rules, preferences, decisions, overrides, provenance, and long-lived continuity state.
- `SkeletonMemory` is operational state for task/run/event snapshots and bounded control-layer bookkeeping.
- Important durable Skeleton context must be classified and persisted through MemoryGateway rather than left only in chat or SkeletonMemory operational rows.
- Before serious work concludes that important context is missing, MemoryGateway must be queried first using exact canonical read/lookup/list, with semantic or graph retrieval used only as derived assistance.
- Canonical writes are complete only after exact MemoryGateway readback verifies the intended record and canonical revision.
- ChatGPT memory is convenience continuity only. It is non-authoritative and is not proof that Skeleton durable state has been written.
- Home Edge is an execution node, not memory authority, unless a reviewed control path explicitly defines it as transport for a specific task.
- Secrets and credentials belong in Bitwarden. MemoryGateway and SkeletonMemory may store only approved opaque secret references, never secret plaintext.

`core/skeleton_memory.py` starts stage 1 with SQLite from the Python standard
library. SQLite stores structured operational state: project state snapshots,
executor runs, operator events, decision records, canon candidates, and opaque
private reference stubs. JSON metadata is stored as text, timestamps are UTC
ISO8601 strings, and ids are UUIDs unless a caller supplies an id.

The store uses safe local defaults. It attempts `PRAGMA journal_mode=WAL`,
does not use network or subprocess calls, and does not choose any live runtime
path by itself. Tests use only `:memory:` or `tmp_path`.

## Boundaries

GitHub remains the public-safe place for canon and handoff material. Canon
promotion starts as a pending `canon_candidates` row and requires explicit
operator approval through `approve_canon_candidate(candidate_id, operator)`.
This operational promotion mechanism does not replace the canonical private
MemoryGateway authority described above.

Secrets stay in Bitwarden. Runtime code may receive approved secret references
or environment injection from the controlled secret path, but plaintext secret
values must not be persisted in SkeletonMemory, MemoryGateway, GitHub, chat, or
plain Drive.

Drawings, private files, and customer documents remain in controlled private
storage. They must not be copied into public GitHub and must not be stored as
raw content in operational SkeletonMemory.

OpenHands and Codex may receive context from Runner, but they do not directly
own or write Skeleton memory. Runner or another controlled server-side caller
is responsible for deciding when to call the memory layer.

Private reference stubs are allowed only as opaque references. They can record
that a controlled private artifact exists, but not its Drive URL, file id, raw
filesystem path, `.env` content, secret value, or private document body.

## Stage 2 Runner Integration

Runner can now write sanitized task lifecycle events into Skeleton memory after
it claims and completes a task. This integration is disabled by default. Runner
does not create or use a live memory path unless one of these explicit runtime
configurations is present:

- `SKELETON_RUNNER_MEMORY_DB` and `SKELETON_RUNNER_MEMORY_LEDGER`
- `SKELETON_RUNNER_MEMORY_DIR`

When `SKELETON_RUNNER_MEMORY_DIR` is used, Runner derives `skeleton.db` and a
monthly `events_YYYY_MM.jsonl` ledger path inside that directory. Suggested
Hetzner runtime paths are:

- `/home/agent/skeleton-memory/skeleton.db`
- `/home/agent/skeleton-memory/events_YYYY_MM.jsonl`

Tests must use only `tmp_path` paths and must not create these runtime files.

Runner records only bounded operational facts:

- task picked up
- executor result status: `DONE`, `BLOCKED`, or `ERROR`
- public GitHub pull request URL, if present
- sanitized relative changed-file list
- pytest summary lines only
- executor name when known: `codex`, `openhands`, or `maintenance`
- issue number
- project id
- runner status

Runner never writes raw Codex or OpenHands transcripts, full test logs, `.env`
values, secret-looking fields or values, Drive or Docs URLs, private filesystem
paths, private document content, or private data locators. The integration uses
the same public-safe validation as `core/audit_ledger.py` and
`core/skeleton_memory.py`; unsafe report content is omitted or reduced to a
redacted summary before append.

Memory writes are best-effort. If SQLite or JSONL append fails, Runner keeps the
task status it already computed and adds a public-safe memory warning to the
task report. A memory write failure must not turn a `DONE` task into `BLOCKED`.
This best-effort rule applies only to operational SkeletonMemory events; it does
not make a required canonical MemoryGateway write optional.

OpenHands and Codex do not directly own or write Skeleton memory in this stage.
They may produce task output for Runner, but Runner is the controlled caller
that extracts public-safe outcome fields and writes the sanitized memory event.

## Controlled Backfill

Runner has an allowlisted maintenance task,
`backfill_skeleton_memory_recent`, for a small recent Skeleton operational
state backfill. It is not automatic and must be run only from an explicit
operator-approved maintenance issue with live memory configuration present.

Backfill records must be explicit, bounded, and public-safe. They must not read
chat logs, private docs, Drive or Docs URLs, private filesystem paths, `.env`
contents, raw logs, secret values, or environment values. The handler may write
only fixed public operational facts into `memory_events`, `project_state`, and
the audit ledger. It does not promote anything to canonical MemoryGateway
state.

The current SkeletonMemory API has no public decision-record writer. This
backfill therefore skips `decision_records`; adding a safe decision-record API
is a follow-up before future backfills should use that table.
