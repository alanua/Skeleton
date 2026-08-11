# Control-Plane Self-Healing

The control-plane recovery layer handles known Runner, Scheduler, and Loop failures through fixed recovery plans. It does not create a second Runner or hidden shell path. Recovery enters through the existing scheduler/shared-dispatch fabric or the existing runtime-maintenance dispatcher.

## Failure Classes

Initial typed classes:

- `CODEGEN_RUNTIME_UNHEALTHY`
- `REGISTERED_CHECKOUT_STALE_OR_DIRTY`
- `LONG_LIVED_POLLER_STALE`
- `EXECUTOR_SERVICE_NOT_RUNNING`
- `GITHUB_ACTIONS_LANE_UNAVAILABLE_BUT_ISSUE_RUNNER_HEALTHY`
- `QUEUE_LABEL_STATE_STUCK`
- `CANARY_FAILED_AFTER_RECOVERY`

Unknown failures and unsafe payloads become `NEEDS_OPERATOR`.

## Fixed Actions

Recovery plans may call only registered action IDs. Those IDs map to existing maintenance task IDs in `core.runner_repository_maintenance_executor`.

- `registered_checkout_recover` -> `recover_skeleton_checkout`
- `registered_checkout_freshness_canary` -> `check_skeleton_freshness`
- `long_lived_poller_reload` -> `sync_telegram_callback_poller_runtime`
- `executor_service_preflight` -> `hermes_worker_preflight`
- `codegen_runtime_recover` -> fixed reviewed Codex runtime recovery primitive
- `codegen_read_only_canary` -> `hermes_worker_preflight`
- `queue_reactivate` -> `replenish_runner_queue`
- `issue_runner_continue` is a typed no-op proving the issue-Runner lane may continue when only GitHub Actions is unhealthy.

Issue payloads cannot supply commands, package names, versions, paths, services, models, scripts, or new authority.

## Execution Model

`core.control_recovery.RecoveryStore` persists one row per failure key in the fixed Runner-owned local-state database:

`/home/agent/.local/state/skeleton-runner/control-recovery/control_recovery.sqlite3`

The recovery authority is not under repository `.codex`, issue worktrees, `/tmp`, or cleanup-owned artifact trees, and issue payloads cannot select the path. The state directory is forced to `0700`; the SQLite file is forced to `0600`. Duplicate ticks and restarts observe the durable row, so a recovered failure does not execute again. Failed recovery moves to `WAITING_RECOVERY` with deterministic backoff. Exhaustion records exactly one durable `NEEDS_OPERATOR` notification flag.

Codex/codegen recovery uses the fixed `codegen_runtime_recover` maintenance action, a read-only canary, and queue reactivation. It does not accept issue-selected commands, models, paths, packages, services, scripts, or fallback providers.

Ordinary Runner codegen failures are bridged into recovery only for fixed known local Codex/runtime incompatibility signatures. The current codegen infrastructure classifier recognizes the exact Codex model metadata decode incompatibility:

`failed to decode models response: unknown variant \`max\``

Provider quota/outage, prompt failures, task implementation failures, and generic Codex failures are not classified as `CODEGEN_RUNTIME_UNHEALTHY`; they continue through the existing bounded fallback and retry policy.

## Queue Resume

Recoverable codegen consumers get one public-safe scheduler occurrence that waits on the stable recovery occurrence. The GitHub issue is moved to the existing `runner:waiting-dependency` label instead of terminal `runner:blocked`. Once recovery is `RECOVERED`, the existing scheduler dependency resumer moves the consumer back to pending dispatch, and the existing queue reactivation action promotes eligible waiting work back to `runner:ready` without a chat `+`.

Protected Runner changes still require a fresh exact-head review before merge; recovery only gets the task back into the normal queue.

## Operator Noise

Retry, recovery, and success do not send Telegram. Telegram/operator relay is reserved for true durable `NEEDS_OPERATOR`.
