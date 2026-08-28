# Runner #3584 protected fix

Scope approved by operator:

- compatibility parsing for fenced `task_kind: runtime_maintenance` tasks without weakening the legacy exact parser;
- register only `runner_controller_repair_codex_state_mount_v1` as a typed privileged action;
- move only Codex `HOME`/`TMPDIR` isolation scratch into a scoped writable Runner-owned directory inside the verified issue worktree, with deterministic cleanup;
- tests for routing, fail-closed action registration/dispatch, writable scoped Codex state, and cleanup;
- no merge, no live deploy, no live privileged repair, no #3584 retry.

Runtime evidence:

- `skeleton-runner-poll.service` has no `ReadOnlyPaths`, `BindReadOnlyPaths`, `TemporaryFileSystem`, or related systemd filesystem restriction;
- Codex is invoked with `--sandbox workspace-write`;
- `RunnerProcessIsolator` currently creates an external `TemporaryDirectory` and assigns it to both `HOME` and `TMPDIR`;
- issue #3584 was misrouted to `code_generation` because the legacy maintenance parser requires exact `Mode: RUNTIME_MAINTENANCE_TASK` plus `Maintenance Task ID:` while the modern fenced task uses `task_kind: runtime_maintenance` and `payload.operation`.

This file is a public-safe implementation receipt for the protected PR branch. It does not authorize deployment or runtime execution.
