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
- `codegen_read_only_canary` -> `hermes_worker_preflight`
- `queue_reactivate` -> `replenish_runner_queue`
- `issue_runner_continue` is a typed no-op proving the issue-Runner lane may continue when only GitHub Actions is unhealthy.

Issue payloads cannot supply commands, package names, versions, paths, services, models, scripts, or new authority.

## Execution Model

`core.control_recovery.RecoveryStore` persists one row per failure key. Duplicate ticks and restarts observe the durable row, so a recovered failure does not execute again. Failed recovery moves to `WAITING_RECOVERY` with deterministic backoff. Exhaustion records exactly one durable `NEEDS_OPERATOR` notification flag.

Codex/codegen recovery never invokes Codex. It uses fixed maintenance actions and a read-only canary, then reactivates the queue.

## Queue Resume

Recoverable blocked consumers should wait on the recovery occurrence. Once recovery is `RECOVERED`, the existing scheduler dependency resumer moves those consumers back to pending dispatch, preserving scheduled priority order.

## Operator Noise

Retry, recovery, and success do not send Telegram. Telegram/operator relay is reserved for true durable `NEEDS_OPERATOR`.
