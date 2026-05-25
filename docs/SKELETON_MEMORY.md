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
