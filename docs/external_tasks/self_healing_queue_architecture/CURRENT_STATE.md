# CURRENT STATE — 2026-08-17

## Repository baseline

- Repository: `alanua/Skeleton`
- Main SHA when this package was created: `60dda6337da32c9f80d1f3a5ac19ae2b9008e507`
- Main already contains the non-protected Execution Fabric core from PR #2880.

Merged core already provides:

- typed `TaskProfile`;
- separate model and executor registries;
- atomic `ExecutionBinding`;
- binding kinds `NO_MODEL | EMBEDDED_MODEL | EXTERNAL_MODEL`;
- immutable `RouteLease`;
- stable failure taxonomy;
- deterministic `DeliverableValidation`;
- production external-model eligibility requires promoted `LIVE` model capability evidence;
- `rc=0` with required missing deliverables is rejected by the core validator.

Important: this core is not yet fully integrated into the protected canonical Runner finalization/reroute path.

## Canonical current architecture direction

Issue #2809 is the architecture authority for Execution Fabric. Its accepted flow is:

`TaskContract -> deterministic TaskProfile -> Policy/Gates -> eligible ExecutionBindings -> deterministic ranking -> immutable RouteLease -> executor attempt -> deterministic DeliverableValidation -> RouteReceipt -> bounded reroute/escalation or DONE`

Scheduler decides WHEN/WHAT is runnable. Execution Fabric decides WHICH already-authorized binding is attempted. Runner/control core owns attempt lifecycle and completion. Models never gain execution authority.

## Existing self-healing mechanisms

`docs/CONTROL_PLANE_SELF_HEALING.md` already documents and partially implements:

- durable control-recovery records in SQLite;
- stable failure classes;
- fixed registered recovery actions;
- bounded backoff;
- durable scheduler occurrence claims with owner, lease expiry and heartbeat;
- passive reconciliation after restart;
- ambiguous mutating receipts -> `NEEDS_OPERATOR` instead of automatic replay;
- queue-idle recovery when `runner:ready=0` and `runner:running=0` but eligible work exists;
- queue reactivation through the existing replenisher rather than direct learned-path label mutation;
- terminal-label pollution reconciliation;
- exact-head PR validation continuation;
- same-PR publication checks;
- protected/high-risk merge fail-closed behavior;
- routine recovery remains Telegram-silent; durable `NEEDS_OPERATOR` is the notification boundary.

## Real remaining defect: false DONE through hidden codegen fallback

The current protected Runner compatibility layer still has an historical Codex→OpenHands fallback inside `core/runner_child_environment.py` on main.

Observed real pattern on multiple P0 tasks:

1. Codex fails because of quota/provider outage.
2. Wrapper invokes OpenHands implicitly.
3. OpenHands exits `0` and prints `RESULT: OK`.
4. No requested repository files are changed.
5. Runner labels the issue `runner:done` anyway.

This has occurred on real tasks including #2777, #2859, #2879, #2885 and #2887 shapes. It is not hypothetical.

A protected safety PR currently exists to remove this hidden fallback until explicit ExecutionBinding integration is ready:

- PR #2890
- exact head at creation: `8786c997ecaad73abfbccf8acb8ea700b31a8973`
- purpose: canonical recovered Codex wrapper becomes Codex-only; provider failure propagates honestly; OpenHands may run only later through explicit ExecutionBinding/RouteLease.
- protected, therefore no merge without exact-head operator approval.

## Current queue/poller pain points

The queue has improved but still needs manual operational attention because:

- some RUN_NOW tasks need a manual `runner:ready` nudge before pickup;
- codegen provider failure can end in false terminal state;
- `runner:done` can mean 'process returned OK' instead of 'deliverable contract accepted';
- stale combinations of `queue:RUN_NOW`, `runner:ready`, `runner:running`, `runner:done`, `runner:blocked` have historically occurred;
- recovery knowledge exists, but task-level retry/reroute/validation ownership is not yet unified with Execution Fabric;
- one blocked P0 can consume attention even when unrelated executable work exists;
- protected integration work itself can be routed through the failing codegen path and therefore fail to repair the path that is failing.

## Current protected Runner integration target

Issue #2885 defines the intended protected Phase B:

- integrate merged Execution Fabric into canonical Runner;
- deterministic maintenance paths remain `NO_MODEL`;
- codegen attempts use explicit production `ExecutionBinding`;
- `RouteLease` fixed before attempt;
- no hidden Codex→OpenHands switching;
- collect concrete changed files/artifacts/test/validation evidence;
- call `validate_deliverable` before any `DONE` label/comment/finalization;
- required nonzero edits + zero changed files => `DELIVERABLE_MISSING` / retry-or-escalate, never DONE;
- expected PR missing => not DONE;
- protected accepted changes => `NEEDS_OPERATOR`;
- bounded attempts only;
- privacy/policy/budget denial never broadens fallback.

Issue #2885 itself was incorrectly marked `runner:done` after OpenHands returned `RESULT: OK` with zero file changes, demonstrating the defect it was intended to fix.

## Relevant current files

Read these on main:

- `scripts/runner_poll_github_tasks.py`
- `core/runner_child_environment.py`
- `core/execution_fabric.py`
- `core/executor_registry.py`
- `core/model_registry.py`
- `core/model_selector.py`
- `core/failure_taxonomy.py`
- `core/control_recovery.py`
- `core/runner_executor_registry.py`
- `core/runner_repository_maintenance_executor.py`
- `core/scheduler_engine.py`
- `core/scheduler_store.py`
- `docs/CONTROL_PLANE_SELF_HEALING.md`
- `docs/EXECUTION_FABRIC.md`

## Review objective

Do not assume the current label/state mechanics are ideal. The review should determine the smallest coherent target state model that unifies:

- queue admission;
- scheduler claims;
- executor/model routing;
- attempt evidence;
- validation;
- publication;
- retry/reroute;
- control-plane repair;
- operator escalation;
- final terminal state.

The desired outcome is not 'more retries'. It is a system that knows exactly what failed, what may be retried, what may be rerouted, what must never be repeated, and how unrelated work continues automatically.
