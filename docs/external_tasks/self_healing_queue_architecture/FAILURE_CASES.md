# FAILURE CASES — the design must handle these explicitly

These are concrete failure shapes or required simulations. For each, specify the owning state transition, durable evidence, retry/reroute rule, cooldown/circuit-breaker effect, and terminal condition.

## F1 — false DONE after provider fallback

- Task requires repository edits and a draft PR.
- Codex fails from provider/quota outage.
- Hidden wrapper invokes OpenHands.
- OpenHands returns `rc=0`, `RESULT: OK`.
- Changed files = 0; PR = absent.
- Existing Runner may label `runner:done`.

Required target behavior: never DONE. Classify provider failure separately from missing deliverable. Reroute only if another eligible binding exists and attempt budget allows; otherwise blocked/cooldown/operator according to policy. Original task remains non-terminal if safe reroute is possible.

## F2 — queue idle while eligible work exists

- `runner:ready=0`
- `runner:running=0`
- at least one eligible P0/P1 issue exists
- no terminal/policy dependency blocks it

Required target behavior: durable idle-with-work detection, race-safe recheck, canonical replenisher invocation, verified ready-depth progress, bounded retry/backoff if no progress. No manual chat/label nudge.

## F3 — stale or contradictory labels

Examples:

- `runner:done` + `runner:ready`
- `runner:blocked` + `runner:running`
- stale `queue:RUN_NOW` on terminal task

Required target behavior: labels are a projection, not source of truth. Durable canonical state wins. Reconciliation repairs projection idempotently without reopening terminal work or losing operator gates.

## F4 — Runner/poller dies during claimed work

- Scheduler occurrence is `running`.
- Process dies / host reboots / service stops renewing heartbeat.
- No clear final GitHub comment was written.

Required target behavior: claim lease expires; durable receipt is inspected. If no side effect and attempt is replay-safe, bounded retry. If successful durable receipt exists, finalize without rerun. If external side effect may have happened, `NEEDS_OPERATOR` / reconciliation probe, never blind replay.

## F5 — provider outage affects one binding only

- Codex/OpenAI quota exhausted or provider unavailable.
- Other already-approved binding may exist.

Required target behavior: mark failure against exact binding/provider capability context; cooldown that route; do not mark executor universally dead; deterministic reroute only to pre-authorized compatible binding; no privacy/budget widening.

## F6 — executor unavailable but model healthy

- Harness binary/service is missing or unhealthy.
- Model/provider itself may be fine.

Required target behavior: executor health and model health remain separate dimensions. Cooldown/repair executor route without poisoning model evidence globally.

## F7 — model incapable / zero useful progress

- Executor/harness runs successfully.
- Model produces no required edit/artifact repeatedly.

Required target behavior: `NO_PROGRESS` / `DELIVERABLE_MISSING`, not provider outage. Bounded escalation to stronger already-approved capability only if TaskProfile policy permits. Repeated hard failure lowers eligibility for that capability, not unrelated capabilities.

## F8 — tests pass but deliverable contract fails

Examples:

- zero changed files where edits were required;
- expected draft PR absent;
- required generated artifact absent;
- wrong PR created;
- wrong exact head published.

Required target behavior: tests are evidence, not completion authority. DeliverableValidation owns acceptance.

## F9 — PR head moves after validation

- exact head `A` validated PASS;
- branch later changes to `B`;
- old PASS exists.

Required target behavior: validation bound to exact head/base. Old receipt becomes stale evidence. Revalidate B before merge-ready state.

## F10 — publication interrupted

- worktree contains valid edits;
- push starts but network fails;
- GitHub state uncertain.

Required target behavior: publication reconciliation by exact branch/head metadata. Never blindly repush mutating result if outcome is ambiguous. Use force-with-lease / exact expected old head or equivalent bounded semantics.

## F11 — protected task implementation succeeds

- changed files and validation are correct;
- touched file is Runner core, gate, workflow, secret/deploy/server/legal/governance or other protected boundary.

Required target behavior: `NEEDS_OPERATOR`, not auto-merge. Queue must continue unrelated tasks while waiting.

## F12 — one blocked task starves queue

- high-priority task waits on credential, provider, operator, hardware scan, dependency, or cooldown.
- unrelated executable tasks exist.

Required target behavior: blocked task is parked with explicit wake condition. It must not monopolize the active slot or prevent replenishment of other domains.

## F13 — dependency becomes satisfied

- task is `WAITING_DEPENDENCY` / equivalent.
- dependency completes or verified recovery finishes.

Required target behavior: deterministic resumer transitions it back to runnable once, preserving priority/fairness and idempotency.

## F14 — external mutation has ambiguous result

Examples: device firmware flash, deploy, finance/legal action, delete, mail send, Home Edge mutation.

Required target behavior: no automatic replay based on timeout alone. Run fixed reconciliation/readback if available; otherwise operator. Side-effect class must affect retry policy.

## F15 — stale/dirty registered checkout

- Runner working tree drifted, stale branch, wrong main SHA, or dirty unexpected files.

Required target behavior: classify infrastructure failure; use fixed registered recovery action; validate exact main afterward; task attempt should not consume model retries while infrastructure is broken.

## F16 — GitHub unavailable

- local Scheduler/Runner state exists but issue/PR API is temporarily inaccessible.

Required target behavior: preserve local durable state, back off, do not infer merge/publish success, do not duplicate work. Resume through reconciliation when GitHub returns.

## F17 — local/private task sees cloud-only healthy route

Required target behavior: no route. `PRIVACY_DENIED` or equivalent remains fail-closed. Self-healing may wait for local executor/model recovery but must never leak data to cloud as fallback.

## F18 — queue recovery itself fails repeatedly

- poller restart does not fix queue;
- replenish action returns no progress;
- same failure recurs.

Required target behavior: bounded recovery generation, backoff, verified lessons/circuit breaker, then durable `NEEDS_OPERATOR`. Avoid infinite recovery loops and Telegram noise for every attempt.

## F19 — service healthy but live acceptance depends on physical event

Example: document worker installed and healthy, but no new physical scan arrives during canary window.

Required target behavior: deployment may be healthy while acceptance canary is `awaiting_physical_event`. Do not fabricate success and do not keep blocking unrelated queue work. Define a wake condition/event/watch task.

## F20 — recovery task itself requires broken codegen path

A protected queue repair is submitted as ordinary codegen while codegen routing is the failing component.

Required target behavior: recovery architecture must have deterministic/no-model escape hatches for repairing registered runtime/control-plane failures. It must not depend on the failing subsystem to repair that subsystem.
