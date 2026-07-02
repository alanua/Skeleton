# Runner Retry Policy

Runner tasks that execute Codex must include concrete `expected_output` before
execution. Missing, empty, and placeholder values are rejected before an issue
worktree is prepared.

Publish retry overrides are one-time operator records. When accepted, the runner
records the bounded override hash in both successful and failed publish reports
so the exact approved scope can be audited without exposing private data.
