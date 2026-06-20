# Universal Runner Tasks

Runner is a passive execution bridge. It does not create tasks, choose roadmap,
infer intent, self-continue, merge, deploy, or activate live runtime changes.
Skeleton/operator must explicitly push every task.

Universal tasks use a fenced JSON envelope:

````text
Mode: UNIVERSAL_RUNNER_TASK

```task
{
  "schema": "skeleton.universal_runner_task.v1",
  "task_id": "task-123",
  "idempotency_key": "task-123-start",
  "action": "START",
  "executor_type": "read_only_probe",
  "capability": "read_only",
  "risk_class": "low",
  "target": {"resource": "docs/UNIVERSAL_RUNNER_TASKS.md"},
  "repo": "alanua/Skeleton",
  "branch": "runner/example",
  "task": "Check public docs only.",
  "allowed_files_or_resources": ["docs/UNIVERSAL_RUNNER_TASKS.md"],
  "forbidden_actions": ["merge", "deploy", "service_restart"],
  "validation": {},
  "expected_output": "aggregate public status",
  "privacy_boundary": "public-safe aggregate status only",
  "timeout_seconds": 300,
  "approval_requirement": "none",
  "private_payload_ref": null
}
```
````

Free-form prose is rejected in universal mode. Unknown executors, capabilities,
and actions fail closed.

## Envelope

Required fields:

- `schema`
- `task_id`
- `idempotency_key`
- `action`
- `executor_type`
- `capability`
- `risk_class`
- `target`
- `repo`
- `branch`
- `task`
- `allowed_files_or_resources`
- `forbidden_actions`
- `validation`
- `expected_output`
- `privacy_boundary`
- `timeout_seconds`
- `approval_requirement`
- `private_payload_ref`

Supported actions:

- `START`
- `STATUS`
- `CONTINUE`
- `CANCEL`

State machine:

- `RECEIVED`
- `PREFLIGHT`
- `RUNNING`
- `CHECKPOINTED`
- `NEEDS_OPERATOR`
- `BLOCKED`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

## Executors

Initial registry entries:

- `codex_branch_task` with `code_change`
- `hermes_private_task` with `private_task`
- `local_module_task` with `registered_command`
- `runtime_maintenance_task` with `runtime_maintenance`
- `read_only_probe` with `read_only`

The registry replaces top-level hardcoded task selection. Each adapter declares
its executor type, capabilities, and actions. A task may execute only when all
three match a server-side registration.

`local_module_task` does not accept arbitrary shell commands from issue text. It
may only invoke command definitions registered on the Runner host.

`codex_branch_task` is registered, but live branch execution remains gated by the
existing issue-worktree Runner path. The universal adapter returns
`NEEDS_OPERATOR` unless a host-side starter is explicitly registered.

`hermes_private_task` is mocked in this implementation. `START`, `STATUS`,
`CONTINUE`, and `CANCEL` resolve only an opaque server-side ref and report public
aggregate status. It does not execute live Hermes tasks.

## Safety

Runner validates schema, executor, capability, action, target shape, allowed
scope, privacy boundary, approval requirement, idempotency, lease, timeout, and
cancellation. Duplicate completed idempotency keys return the stored result.
Concurrent duplicates are locked and blocked.

Protected and high-risk tasks require explicit approval evidence. Tests passing
never authorizes merge. Merge and live activation remain separate operator gates.

Public GitHub comments contain only public-safe metadata, opaque references, and
aggregate status. Private payloads are resolved only on the Runner host and are
not expanded into public output.
