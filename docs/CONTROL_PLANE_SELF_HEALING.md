# Control-Plane Self-Healing

The control-plane recovery layer handles known Runner, Scheduler, Loop, and codegen-runtime failures through fixed recovery plans. It does not create a second Runner or hidden shell path. Recovery enters through the existing Scheduler/shared-dispatch fabric or the existing runtime-maintenance dispatcher.

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

Recovery plans may call only code-owned registered action IDs. Issue payloads cannot select commands, package names, versions, paths, services, models, scripts, or new authority.

- `registered_checkout_recover` -> existing `recover_skeleton_checkout` maintenance primitive
- `registered_checkout_freshness_canary` -> existing `check_skeleton_freshness` primitive
- `long_lived_poller_reload` -> fixed canonical `skeleton-runner-poll` timer/service recovery
- `executor_service_preflight` -> fixed canonical Runner executor-service recovery
- `codegen_runtime_recover` -> fixed Codex runtime pin/verify/rollback path with exact code-owned target version
- `codegen_read_only_canary` -> exact verified pinned Codex read-only canary; OpenHands is allowed only for genuine provider/quota unavailability after the Codex client reaches normal provider handling
- `queue_reactivate` -> existing `replenish_runner_queue` primitive
- `issue_runner_continue` -> typed continuation when only the GitHub Actions lane is unhealthy and the issue Runner itself remains healthy

The automatic codegen-runtime classifier is intentionally narrow. It only treats a nonzero Codex run as `CODEGEN_RUNTIME_UNHEALTHY` when the output contains the confirmed live metadata failure text:

```text
failed to decode models response: unknown variant `max`
```

Harmless prefix, suffix, and version noise may surround that exact phrase. Generic Codex failures, prompt/task failures, sqlite read-only errors, permission failures, quota, and provider outages do not enter this classifier.

## Codex Runtime Boundary

The compatible Codex executable is derived from the fixed npm-global runtime (`npm prefix -g/bin/codex`), not from a potentially stale `codex` resolved through the Runner service PATH. Recovery pins the exact reviewed version, verifies the installed semantic version, and runs a read-only smoke.

A true client/schema smoke failure rolls the mutation back to the exact previous semantic version. A provider quota/outage proves the compatible client got past local metadata decoding; it therefore keeps the compatible pin and leaves provider fallback policy to the independent canary.

After successful recovery, a local non-secret marker records the exact pinned version. Normal codegen child construction uses that marker only as a cheap precondition, then revalidates the exact npm-global Codex path/version before installing the bounded child wrapper. Recovery authority (`HOME`, `PATH`, systemd invocation identity, pinned-path selection and wrapper storage root) comes only from the actual Runner process environment; caller-supplied child environment overlays cannot replace it. The wrapper always invokes that exact Codex path even if the parent service PATH had previously resolved a stale binary. Home Edge executor/HMAC values are stripped from recovery and codegen child environments.

## Execution Model

`core.control_recovery.RecoveryStore` persists one row per failure key. Duplicate ticks and restarts observe the durable row, so a recovered failure does not execute again. Failed recovery moves to `WAITING_RECOVERY` with deterministic backoff. Exhaustion records exactly one durable `NEEDS_OPERATOR` notification flag.

Scheduler dispatch uses durable single-owner claims. A pending occurrence is atomically moved to `running` with an attempt number, idempotency key, claim owner, lease expiry, and heartbeat timestamp. While the existing shared dispatcher is actively handling that occurrence, `SchedulerEngine` starts one bounded renewal thread for that claim only. The renewal extends the same owner lease before expiry and stops in a `finally` block when dispatch completes, fails, or is cancelled. A killed process naturally stops renewing, so the occurrence is recoverable only after the stored lease expires.

Startup and ordinary Runner polls perform passive scheduler reconciliation without a dispatcher. Reconciliation first checks expired running occurrences for durable dispatch receipts. A successful receipt finalizes the occurrence as done without re-executing the dispatcher. A non-success receipt that reports external side effects becomes durable `NEEDS_OPERATOR` with reason `AMBIGUOUS_MUTATING_RECEIPT`; it is not replayed. Receipt-less expired work is retried only up to the bounded scheduler attempt limit, then escalates to `NEEDS_OPERATOR`.

Production state is code-owned local agent state:

- control recovery DB: `/home/agent/.local/state/skeleton-runner/control-recovery/control_recovery.sqlite3`
- scheduler DB: `/home/agent/.local/state/skeleton-runner/scheduler/scheduler.sqlite3`

Production recovery state never uses `/var/lib`, the repository, an issue worktree, `.codex`, `/tmp`, or an issue/environment-controlled path. Regression tests may monkeypatch the runner module `ROOT`; only that explicit synthetic mode moves recovery/scheduler state under the monkeypatched root so tests cannot observe persistent live rows.

For `CODEGEN_RUNTIME_UNHEALTHY`, the fixed order is:

`codegen_runtime_recover -> codegen_read_only_canary -> queue_reactivate`

The recovery action itself is codegen-independent. The canary is verification, not the repair mechanism.

## Queue Resume

Recoverable blocked consumers should wait on the recovery occurrence. Once recovery is `RECOVERED`, the existing Scheduler dependency resumer moves those consumers back to pending dispatch, preserving scheduled priority order.

Ordinary Runner codegen failures classified by the exact live metadata phrase enter the existing recovery route before terminal blocking. The issue is moved to `runner:waiting-dependency`, a durable recovery occurrence is recorded, and a same-issue consumer waits on it. After `codegen_runtime_recover` and the read-only Codex canary succeed, `queue_reactivate` removes `runner:waiting-dependency` from that same GitHub issue and adds `runner:ready`.

During each ordinary Runner poll, the single poller also checks open `agent:task` issues marked `queue:RUN_NOW` that are missing `runner:ready`. Valid public-safe Skeleton task issues that are not running, waiting on dependencies, terminal, operator-held, duplicate, dependency-blocked, or otherwise excluded by the existing queue policy are idempotently promoted with `runner:ready` before normal ready-issue selection. This only restores eligibility for the existing Runner path; it does not add a second queue, bypass RunnerGate, or weaken route, privacy, approval, runtime, protected-file, secret, merge, or operator gates.

## Operator Noise

Retry, recovery, and success remain silent. Operator notification is reserved for true durable `NEEDS_OPERATOR` after bounded recovery is unavailable or exhausted.
