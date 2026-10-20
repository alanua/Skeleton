# TASK — design Skeleton's self-healing execution queue

You are acting as an independent senior distributed-systems / agent-runtime architect.

## Problem

Skeleton has one canonical Scheduler/Runner control plane and a growing set of deterministic maintenance executors, code-generation harnesses, Home Edge executors, GitHub maintenance operations, model/provider routes, and protected operator-gated actions.

The queue works, but it is not yet reliably self-healing. In practice it can:

- stop making useful progress after executor/model/provider failure;
- mark an attempt `DONE` when the requested deliverable was not produced;
- leave stale or contradictory queue labels;
- require manual label nudges to resume work;
- retry the wrong layer instead of classifying the failed layer;
- repeat a known-bad route instead of cooling it down and selecting another already-authorized route;
- confuse infrastructure recovery, task retry, route retry, validation retry, and operator approval;
- become idle while eligible work exists;
- over-trust process return code / provider text instead of durable deliverable evidence.

A concrete current defect is an historical hidden Codex→OpenHands fallback: Codex can fail from quota/provider outage, OpenHands can return `rc=0 / RESULT: OK` with zero repository changes, and the existing Runner can incorrectly treat that as completion. This exact failure has occurred repeatedly on real P0 tasks.

## Your task

Design a production-grade self-healing queue architecture for Skeleton that fits the existing authority model and can evolve over the next several years.

The design must answer, concretely:

1. What is the canonical task state machine?
2. What is the canonical attempt/route state machine?
3. Which component owns `DONE`, and what exact evidence is required?
4. How are failures classified by layer: task, queue, scheduler, executor, harness, model/provider, validation, publication, runtime, external side effect, policy/operator gate?
5. Which failures are retryable, reroutable, recoverable, cooldown-worthy, terminal, or operator-required?
6. How does queue self-healing detect `idle-with-work`, stuck claims, stale labels, crashed pollers, stale checkouts, provider outages, dead executors, incomplete publications, and ambiguous mutating outcomes?
7. How are retries bounded so the system does not loop forever or amplify side effects?
8. How are idempotency, leases, heartbeats, durable receipts, and recovery generations represented?
9. How should `ExecutionBinding`, `RouteLease`, `DeliverableValidation`, executor health, model health, and queue health interact?
10. How should health/circuit-breakers work without letting LLM self-reports control routing?
11. How should deterministic/no-model work be preferred and protected from unnecessary LLM routing?
12. How should local/cloud privacy constraints be enforced during reroute?
13. What exact conditions allow automatic reroute versus `NEEDS_OPERATOR`?
14. How should protected/high-risk tasks behave after successful implementation and validation?
15. How should the system recover after Runner restart, host reboot, network partition, GitHub outage, provider quota, corrupted worktree, partially published PR, or interrupted Home Edge mutation?
16. How should queue replenishment decide what to run next across domains without starvation or one domain monopolizing the queue?
17. What metrics/SLOs are necessary to know whether the queue is actually healthy?
18. What minimum architecture would get Skeleton from current state to ~9/10 reliability with the least risky migration?
19. What parts of the current architecture should be removed, not extended?
20. Which invariants should be machine-enforced in gates/tests rather than documented conventions?

## Required design qualities

The proposal must be:

- single-control-plane, not a second Scheduler/Runner;
- deterministic where possible;
- event/state driven, not dependent on chat/manual prompting;
- durable across process/host restarts;
- bounded and idempotent;
- fail-closed for privacy, policy, budget, credentials, and ambiguous side effects;
- able to reroute only among already-authorized bindings;
- capable of keeping unrelated queue work moving while one task is blocked;
- explicit about ownership and transitions;
- implementable incrementally in the current Python/GitHub/SQLite/systemd architecture;
- future-compatible with local LLMs, Codex, OpenHands, Claude, Kimi, Gemini and future executors/models without binding authority to vendor names.

## Do not do this

Do not propose:

- a second queue, second Scheduler, second policy engine, or second secret store;
- generic arbitrary shell recovery from issue text;
- retries based only on `rc != 0` / `rc == 0`;
- automatic widening of permissions/privacy/budget after failures;
- infinite retry loops;
- automatic merge/deploy for protected/high-risk areas;
- LLM-generated authority fields;
- model/provider selection directly from free-form issue prose;
- 'just add more monitoring' without a state/recovery model;
- a design where GitHub labels are the only durable task state.

## Architecture baseline you should preserve

Canonical direction already accepted in Skeleton:

`TaskContract -> TaskProfile -> Policy/Gates -> eligible ExecutionBindings -> deterministic ranking -> immutable RouteLease -> attempt -> deterministic DeliverableValidation -> RouteReceipt -> bounded reroute/escalation/DONE`

Scheduler owns WHEN/WHAT becomes runnable. Execution Fabric owns WHICH eligible binding is attempted. Runner/control core owns the attempt/validation lifecycle. Executors/models do not grant themselves authority.

You may change the internal representation, split components, introduce additional durable records, or recommend replacing current label-driven mechanics as long as these authority boundaries remain intact.

## Expected result

Return a concrete target architecture, not only critique. Include state diagrams/tables, recovery rules, data model, pseudo-APIs, migration phases, test strategy, and a prioritized implementation plan.

Use `DELIVERABLE_CONTRACT.md` exactly for your answer structure.
