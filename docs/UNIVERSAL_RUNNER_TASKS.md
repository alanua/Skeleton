# Universal Runner Tasks

Universal Runner tasks use canonical schema id `skeleton.runner_task.v1`.
Legacy ids `skeleton.universal_runner_task.v1` and
`skeleton.runner_task.preview.v1` are accepted only through compatibility
normalization and are persisted back as the canonical schema.

## Envelope

Canonical envelopes separate lifecycle from executor identity:

- `action`: `START`, `STATUS`, `CONTINUE`, or `CANCEL`.
- `executor_type`: `codex_branch_task`, `hermes_private_task`,
  `local_module_task`, `runtime_maintenance_task`, or `read_only_probe`.

They also declare `task_id`, `task_key`, `risk`, `payload`, optional
`resources`, optional verified `approval_evidence`, optional `allowed_scope`,
`forbidden_actions`, `validation`, `privacy_boundary`, and optional
`timeout_seconds`.

Canonical envelopes are validated against
`schemas/universal_runner_task.schema.json`. Required fields must be present and
unknown top-level properties are rejected. Legacy `mode` payloads are converted
at the adapter boundary to canonical `action=START` plus `executor_type`.

## Safety

Risk is explicit:

- `LOW` runs without verified approval unless protected resources are detected.
- `YELLOW` runs without verified approval unless protected resources are
  detected.
- `RED` requires verified approval evidence.

`operator_approved` and `operator_approval` are not trusted approval evidence.
Privileged work must include verified `approval_evidence` from an approved
operator event or a signed Telegram callback boundary.

Protected resources are detected recursively from declared resources, structured
payload fields, task prose, scope fields, validation fields, and the privacy
boundary. Protected resources include absolute or user paths, traversal, private
or secrets paths, Runner core, `scripts/runner_poll_github_tasks.py`,
`core/action_gate.py`, `core/gate_engine.py`, control manifests, workflow files,
secrets, deploy/server/finance/legal/governance paths, adapter boundaries, and
Google Drive or Docs references. Protected resources require verified approval
evidence at any risk level.

No executor runs arbitrary shell text from an issue. `local_module_task`,
`runtime_maintenance_task`, and `read_only_probe` are restricted to registered
command ids. `hermes_private_task` is restricted to a server-owned Hermes
registry adapter. `codex_branch_task` is routed by the GitHub poller to the
existing bounded issue-worktree Codex route.

Every adapter receives the same structured runner context: allowed scope,
forbidden actions, validation requirements, privacy boundary, timeout, action,
executor type, task id/key, risk, and approval evidence metadata.

## Process Timeout

Universal task execution uses a stoppable child-process boundary. On timeout the
Runner terminates the worker, escalates to kill if needed, clears the running
lease on final state persistence, and reports `CANCELLED` with a deterministic
timeout reason. Work after the timeout cannot mutate parent task state.

## State

Runner task state is stored in an atomic JSON document guarded by an advisory
lock. Updates are written to a temporary file, fsynced, and atomically replaced.
Concurrent updates for different task keys share the same lock to avoid partial
or lost JSON writes.

Running records carry a lease with owner and heartbeat timestamp. A stale lease
is marked failed and replaced by the new owner before work resumes. Heartbeats
renew the lease so a task running within its allowed timeout does not become
stale prematurely.

`STATUS` and `CANCEL` preserve executor identity and task idempotency. They fail
closed when the task key has no existing state record, returning `FAILED` with
reason `missing_record`.

`CONTINUE` resumes only a persisted `CHECKPOINTED` record. Missing checkpoints
fail closed with reason `checkpoint_missing`.

`NEEDS_OPERATOR` and `CHECKPOINTED` are persisted before returning so later
`STATUS` and `CONTINUE` requests can retrieve them.

## GitHub Status Mapping

| Universal state | GitHub state | Next action | Meaning |
| --- | --- | --- | --- |
| `CHECKPOINTED` | `pending` | `CONTINUE` | Work reached a resumable checkpoint. |
| `NEEDS_OPERATOR` | `action_required` | `START_WITH_APPROVAL` | Operator approval or review is required. |
| `RUNNING` | `pending` | `STATUS` | Work is active or leased. |
| `CANCELLED` | `cancelled` | `START` | Work was cancelled or timed out. |
| `FAILED` | `failure` | `START` | Work failed closed. |
| `COMPLETED` | `success` | `NONE` | Work completed. |

## Compatibility Matrix

| Legacy input | Compatibility behavior |
| --- | --- |
| `skeleton.universal_runner_task.v1` | Accepted and migrated to `skeleton.runner_task.v1`. |
| `skeleton.runner_task.preview.v1` | Accepted and migrated to `skeleton.runner_task.v1`. |
| `codex_issue_worktree` | Routed as `action=START`, `executor_type=codex_branch_task`. |
| `local_command` | Routed as `action=START`, `executor_type=local_module_task` and still requires a registered command id. |
| `hermes_task` | Routed as `action=START`, `executor_type=hermes_private_task`. |
| `RUNNER_TASK` issue fence | Preserved through the existing Codex worktree adapter. |
| `RUNTIME_MAINTENANCE_TASK` issue mode | Preserved through the runtime maintenance adapter and can also be represented as `executor_type=runtime_maintenance_task`. |
| Missing `STATUS` record | Fails closed as `FAILED`. |
| Missing `CANCEL` record | Fails closed as `FAILED`. |

## Still Gated

Live merge, deploy, service restart, runtime sync, private task execution,
unrestricted shell execution, protected-resource writes, and any task relying on
self-declared approval remain gated. They require verified operator evidence and
an allowlisted adapter boundary before execution.
