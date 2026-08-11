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

A model-metadata/schema incompatibility such as an unsupported reasoning-level value is a Codex runtime failure. It must be repaired and verified by Codex itself; it must not be masked by a fallback provider.

## Codex Runtime Boundary

The compatible Codex executable is derived from the fixed npm-global runtime (`npm prefix -g/bin/codex`), not from a potentially stale `codex` resolved through the Runner service PATH. Recovery pins the exact reviewed version, verifies the installed semantic version, and runs a read-only smoke.

A true client/schema smoke failure rolls the mutation back to the exact previous semantic version. A provider quota/outage proves the compatible client got past local metadata decoding; it therefore keeps the compatible pin and leaves provider fallback policy to the independent canary.

After successful recovery, a local non-secret marker records the exact pinned version. Normal codegen child construction uses that marker only as a cheap precondition, then revalidates the exact npm-global Codex path/version before installing the bounded child wrapper. Recovery authority (`HOME`, `PATH`, systemd invocation identity, pinned-path selection and wrapper storage root) comes only from the actual Runner process environment; caller-supplied child environment overlays cannot replace it. The wrapper always invokes that exact Codex path even if the parent service PATH had previously resolved a stale binary. Home Edge executor/HMAC values are stripped from recovery and codegen child environments.

## Execution Model

`core.control_recovery.RecoveryStore` persists one row per failure key. Duplicate ticks and restarts observe the durable row, so a recovered failure does not execute again. Failed recovery moves to `WAITING_RECOVERY` with deterministic backoff. Exhaustion records exactly one durable `NEEDS_OPERATOR` notification flag.

Runner-owned self-healing state lives under `/home/agent/.local/state/skeleton-runner/`, outside repositories, issue worktrees, `.codex`, temporary directories, and runtime installer roots. Control recovery uses `control-recovery/control_recovery.sqlite3`; Runner Scheduler state uses `scheduler/scheduler.sqlite3`. Both are ordinary agent-writable local-state files with private parent/file modes, so normal Runner recovery does not require root, `chown`, or `/var/lib/skeleton` mutation.

For `CODEGEN_RUNTIME_UNHEALTHY`, the fixed order is:

`codegen_runtime_recover -> codegen_read_only_canary -> queue_reactivate`

The recovery action itself is codegen-independent. The canary is verification, not the repair mechanism.

## Queue Resume

Recoverable blocked consumers should wait on the recovery occurrence. Once recovery is `RECOVERED`, the existing Scheduler dependency resumer moves those consumers back to pending dispatch, preserving scheduled priority order.

The remaining integration requirement is that ordinary Runner codegen failures classified as a known recoverable failure must enter this existing recovery route automatically rather than becoming terminal `runner:blocked`. That wiring belongs in the canonical Runner/Scheduler continuation path; it must not be implemented as a second recovery queue or external control plane.

## Operator Noise

Retry, recovery, and success remain silent. Operator notification is reserved for true durable `NEEDS_OPERATOR` after bounded recovery is unavailable or exhausted.
