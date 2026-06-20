# Universal Runner Tasks

Universal Runner tasks use canonical schema id `skeleton.runner_task.v1`.
Legacy ids `skeleton.universal_runner_task.v1` and
`skeleton.runner_task.preview.v1` are accepted only through compatibility
normalization and are persisted back as the canonical schema.

## Execution Boundary

The task envelope declares `task_id`, `task_key`, `mode`, `risk`, `payload`,
optional `resources`, optional `operator_approved`, and optional
`timeout_seconds`.

Risk is explicit:

- `LOW` runs without operator approval unless protected resources are detected.
- `YELLOW` runs without operator approval unless protected resources are
  detected.
- `RED` requires operator approval.

Protected resources are detected from declared resources and common payload file
fields. Protected resources include absolute or user paths, traversal, private
or secrets paths, `.env`, protected governance files, workflow files, and
Google Drive or Docs references. Protected resources require operator approval
at any risk level.

`local_module_task` is restricted to registered command ids. Issue text or JSON
payload command strings are not shell input and are never executed as arbitrary
commands.

`hermes_task` is restricted to a server-owned Hermes registry adapter. The
public adapter response is validated and fails closed if the registry returns
private-looking fields, credentials, paths, Drive references, or raw private
payload content.

`codex_branch_task` is routed by the GitHub poller to the existing bounded
issue-worktree Codex route. It does not introduce a separate checkout, clone, or
shell executor.

## State

Runner task state is stored in an atomic JSON document guarded by an advisory
lock. Updates are written to a temporary file, fsynced, and atomically replaced.
Concurrent updates for different task keys share the same lock to avoid partial
or lost JSON writes.

Running records carry a lease with owner and heartbeat timestamp. A stale lease
is marked failed and replaced by the new owner before work resumes.

`STATUS` and `CANCEL` fail closed when the task key has no existing state
record. They return `FAILED` with reason `missing_record`; they do not invent a
record or report success.

## GitHub Status Mapping

| Universal state | GitHub state | Meaning |
| --- | --- | --- |
| `CHECKPOINTED` | `pending` | Work reached a resumable checkpoint. |
| `NEEDS_OPERATOR` | `action_required` | Operator approval or review is required. |
| `RUNNING` | `pending` | Work is active or leased. |
| `CANCELLED` | `cancelled` | Work was cancelled or timed out. |
| `FAILED` | `failure` | Work failed closed. |
| `COMPLETED` | `success` | Work completed. |

## Compatibility Matrix

| Legacy input | Compatibility behavior |
| --- | --- |
| `skeleton.universal_runner_task.v1` | Accepted and migrated to `skeleton.runner_task.v1`. |
| `skeleton.runner_task.preview.v1` | Accepted and migrated to `skeleton.runner_task.v1`. |
| `codex_issue_worktree` | Routed as `codex_branch_task`. |
| `local_command` | Routed as `local_module_task` and still requires a registered command id. |
| Missing `STATUS` record | Fails closed as `FAILED`. |
| Missing `CANCEL` record | Fails closed as `FAILED`. |
