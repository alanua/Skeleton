# Skeleton Memory

Skeleton operational memory is server-side state for the control layer. It is
not Jeeves memory, private document storage, or a canon write path.

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

Secrets stay only in protected runtime environment variables or a secrets
manager. Drawings, private files, and customer documents remain in private
Drive or a controlled private-data workdir. They must not be copied into public
GitHub and must not be stored as raw content in the memory database.

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

OpenHands and Codex do not directly own or write Skeleton memory in this stage.
They may produce task output for Runner, but Runner is the controlled caller
that extracts public-safe outcome fields and writes the sanitized memory event.
