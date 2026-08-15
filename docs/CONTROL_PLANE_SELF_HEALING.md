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
- `QUEUE_IDLE_WITH_ELIGIBLE_WORK`
- `AMBIGUOUS_MUTATING_RESULT`

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

The same store also persists public-safe `failure_lessons` and aggregate learning metrics. A lesson is keyed by a stable bounded fingerprint derived from failure class and safe context fields, never from raw output, private paths, credentials, or a live candidate list. Each independent occurrence still gets its own failure key for retry/backoff and duplicate suppression.

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

During each ordinary Runner poll, the single poller has one learned queue-idle path. It first requires the live global gate `runner:ready` depth `0` and `runner:running` depth `0`, then finds eligible public-safe queue work through the existing replenisher selection rules. Immediately before mutation it repeats the same ready/running/eligible recheck. Fresh running work suppresses recovery; running work that appears between snapshot and mutation records failed recovery/backoff and performs no mutation.

Terminal Runner codegen completion also re-enters that same learned queue-idle path. After a codegen task is labeled `runner:done` or `runner:blocked`, including explicit blocked Codex output and finalization failures, the completed issue remains terminal while the poller re-evaluates the canonical queue for unrelated eligible work. The validation-continuation maintenance path uses the same terminal hook. The continuation does not add chat/manual-ready requirements, does not re-promote the terminal issue, and does not send Telegram noise.

Acceptance note: the post-merge live canary on main `9d17100369b229c2ed0266a04f3a084952101cc0` was picked up autonomously from RUN_NOW-only queue admission.

For queue-idle recovery, the lesson fingerprint remains stable across recurrences of the same public-safe failure class. The occurrence-specific failure key includes the eligible issue numbers and a verified-lesson generation. Retries and backoff within one idle episode reuse that occurrence key. After an actual recovery is VERIFIED, a later independent recurrence, including the same eligible set becoming eligible again, derives a new occurrence key and can reuse the verified lesson.

The learned queue path may invoke only the registered `queue_reactivate` action. That action delegates to the existing `replenish_runner_queue` primitive and never directly promotes labels from the learned path. Verification requires actual ready-depth progress after the action. No progress, exceptions, candidate races, or stale running rechecks are recorded as failed recovery/backoff. Routine queue recovery remains Telegram-silent.

Ordinary polls also run a bounded reconciliation pass for historical terminal
issue label pollution. Open issues carrying `runner:done` or `runner:blocked`
and any active execution label (`queue:RUN_NOW`, `runner:ready`,
`runner:running`) have only those active labels removed. The pass does not close
issues, does not change PR review state, and is idempotent. Terminal, malformed,
or stale `queue:RUN_NOW` issues are skipped during intake so they cannot prevent
selection of another valid RUN_NOW candidate.

When a successful codegen task reports a public GitHub PR, the Runner verifies
that PR through GitHub metadata and creates or reuses one idempotent
`validate_pr_branch` continuation bound to the exact PR number, head SHA, and
recorded base SHA. The continuation is an ordinary public-safe `agent:task` with
`queue:RUN_NOW` admission metadata and remains Telegram-silent. If the task
contract declared `existing_pr` or `update_existing_pr`, a different produced PR
is treated as a publication-contract failure; no validation continuation is
created for the wrong PR.

When a Runner-produced PR already has successful exact-head validation but its
recorded base is behind current `main`, the canonical `preflight_pr_refresh`
maintenance route re-checks the live PR number, head SHA, recorded validation
head, and recorded validation base before any mutation. A clean semantic replay
refreshes the same PR branch with `--force-with-lease` and queues exact-head
validation for the refreshed head. A real content conflict creates one bounded
`update_existing_pr` repair task against the verified old head and current main;
it does not create a replacement PR. Validation metadata mismatch fails closed.

Only public-safe, risk:green, unprotected PRs with no review or CI failure, no
dependency or overlap hold, and no high-risk activation may be transitioned from
draft to ready and squash-merged automatically with an exact head match. Private,
yellow, red, protected, or high-risk work emits a `NEEDS_OPERATOR` gate instead.
After an automatic merge, the Runner enqueues `runtime_sync_main` so the
long-lived checkout cannot remain stale. Device, provider, deployment, finance,
or legal activation continues only when the task already contains an explicit
registered, authorized, policy-safe activation plan.

For codegen tasks that declare `existing_pr` or `update_existing_pr` with an
exact expected PR head SHA, the issue worktree is materialized from that verified
PR head branch before Codex starts. Metadata mismatch fails closed before any
worktree mutation. The checkout preserves the complete PR-head tree, including
files outside the task `allowed_files`; Codex remains restricted to editing only
the declared allowed files.

Queue replenishment still targets a depth of three to six ready tasks, but
selection now also treats an explicit `domain`, then `project`, then operation
or first path segment as an occupancy key. Ready and running work reserve their
domains, terminal and superseded tasks stay ineligible, and selected tasks must
remain public-safe with non-overlapping `allowed_files`.

## Operator Noise

Retry, recovery, and success remain silent. Operator notification is reserved for true durable `NEEDS_OPERATOR` after bounded recovery is unavailable or exhausted.
