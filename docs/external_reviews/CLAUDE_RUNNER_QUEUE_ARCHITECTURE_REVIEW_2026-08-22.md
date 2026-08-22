# Claude Architecture Review Request — Skeleton Runner Queue

Repository: `alanua/Skeleton`

You are acting as an independent architecture reviewer for the Skeleton control plane.

## Review rules

- Treat the CURRENT GitHub state as the source of truth. Do not rely only on this document.
- Inspect current `main`, relevant issues, PRs, diffs, tests, comments and Runner contracts before reaching conclusions.
- Green tests are NOT sufficient evidence of architectural correctness.
- Do NOT propose a second Scheduler, Runner, queue, publisher, recovery store, or parallel control plane.
- Protected/runtime actions must remain operator-gated.
- Prefer the smallest strong architectural correction over accumulating more patches.
- Do not modify the repository. This is architecture review only.

## Current reference point

- PR #3199 was merged and live-canary verified the dependency rule: READY/RUNNING must not satisfy dependencies; only genuinely completed dependency state may do so.
- Current work around #3228 addresses cross-project durable delivery.
- #3229 addresses Production QA Phase 1.
- Queue architecture programme: #2926.
- Several previous attempts were rejected even with 3000+ passing tests because semantic/runtime contracts were incomplete.

## Real production failure modes observed

### 1. False dependency completion
A task in `runner:ready` could incorrectly satisfy another task's dependency.

### 2. False terminal DONE
Codex could explicitly return `RESULT: BLOCKED` while Runner later converted the task into `runner:done`.

### 3. Lost cross-project deliverable
Code changes could exist successfully in a retained/local target worktree, but the source Skeleton task could become DONE without a durable PR being created in the target repository.

### 4. Liveness / READY without pickup
There can be work in READY with no active consumer. A recovery mechanism implemented only inside `poll_once()` is logically incapable of recovering a poller that is not running.

### 5. Misleading green tests
Several implementations passed the full test suite but their tests used simplified/substituted fixtures rather than the exact live production TaskSpec shapes.

## Proposed target architecture to review critically

### A. One terminal-state writer
Only one authoritative component may transition a task to terminal DONE.

Required proof chain should be approximately:

`execution result -> validation -> deliverable verification -> publication/durable handoff when required -> trusted receipt -> cleanup -> terminal DONE`

Any missing or ambiguous step must remain non-terminal.

### B. Durable task state
Canonical execution state should eventually live in a durable transactional store, likely SQLite:

- `TaskRecord`
- `ExecutionAttempt`
- `RouteLease`
- `DeliverableReceipt`
- `RetryState` / `FailureState`

GitHub labels should be projections/views, not the canonical execution state.

### C. Cross-project delivery contract
For tasks targeting BauClock, Lavalamp, Home Edge or another repository:

`RESULT:DONE + changed files` must NOT mean terminal DONE.

Required flow:

`changed worktree -> exact changed paths derived -> paths proven inside declared TaskSpec allowed scopes -> deterministic publication continuation -> target draft PR created -> trusted publication receipt verifies repo/source/base/branch/head/PR/files -> cleanup -> source DONE`

Declared scopes/globs must never be confused with actual changed-file receipts.

### D. Explicit terminal result truth
An explicit `RESULT: BLOCKED` or `RESULT: NEEDS_OPERATOR` must never be converted to DONE by fallback/local-success logic.

Fallback classification may only operate when there is no authoritative explicit terminal result.

### E. Independent liveness
Liveness/recovery cannot depend solely on code executing inside the poller it is supposed to recover.

We want one canonical Runner/poller, but independent liveness evidence such as:

- last poll
- last successful pickup
- oldest READY age
- ready count
- running count
- timer/service state
- heartbeat/lease freshness

Recovery must trigger only when staleness is independently demonstrated, not merely because `running == 0`.

### F. Production canaries
After every protected Runner/control-plane merge + runtime sync, production-current should require bounded live canaries such as:

- dependency semantics
- RUN_NOW promotion/pickup
- explicit BLOCKED remains non-DONE
- cross-project durable publication
- stale-ready recovery
- restart/reboot continuity

Failure of a required canary should mean the new runtime is not production-proven.

## Your task

Critically review this design against the ACTUAL current Skeleton repository.

Do not merely agree with it.

Specifically find:

- race conditions;
- crash windows;
- double-DONE/double-cleanup risks;
- duplicate publication risks;
- dependency deadlocks;
- ABA/stale-state problems;
- places where GitHub labels and SQLite state could diverge;
- recovery loops;
- places where a dead poller cannot recover itself;
- forged/untrusted receipt possibilities;
- stale-head/base-SHA problems;
- cross-repository delivery loss;
- situations where a successful test suite could still mask a broken production contract;
- unsafe assumptions around systemd timer/service semantics;
- problems caused by using GitHub comments as receipts;
- problems with idempotency and replay after crash/reboot.

Also answer these architecture questions:

1. Is a single SQLite-backed `TaskRecord / ExecutionAttempt / RouteLease` model the right canonical direction for Skeleton, or is there a better minimal architecture?
2. What should be the exact terminal-state state machine? Give explicit states and legal transitions.
3. What data must be committed atomically before/after claiming a task, starting execution, publication, cleanup, and DONE?
4. How should GitHub labels/comments be projected from canonical state without becoming authority again?
5. How should cross-project publication be modeled so a crash at ANY point does not lose successfully generated code?
6. What is the strongest simple design for independent Runner liveness/self-heal without creating a second Runner or control plane?
7. Which canaries are genuinely necessary, and which are unnecessary complexity?
8. Which parts of the current proposed design should be rejected or simplified?
9. What migration sequence would you recommend from CURRENT main with minimum operational risk?
10. What are the top 5 architectural changes that would move Skeleton queue reliability from approximately 7.5–8/10 to 9+/10?

## Required output format

1. **VERDICT:** `APPROVE` / `REQUEST_CHANGES` / `BLOCKED`
2. **CRITICAL FINDINGS** — only concrete issues, ordered by severity.
3. **RECOMMENDED TARGET STATE MACHINE** — explicit states/transitions.
4. **DURABLE DATA MODEL** — minimal required records/fields and atomicity boundaries.
5. **LIVENESS / SELF-HEAL DESIGN** — exact ownership and trigger mechanism.
6. **CROSS-PROJECT DELIVERY DESIGN** — crash-safe end-to-end sequence.
7. **CANARY MATRIX** — what each canary proves and what it cannot prove.
8. **MIGRATION PLAN** — small ordered steps from current main; avoid a rewrite if possible.
9. **DO-NOT-DO LIST** — architectural approaches that would make Skeleton worse.
10. **FIRST NEXT STEP** — exactly one concrete implementation task that should be done first.

Be adversarial. Prefer finding flaws over confirming assumptions.
