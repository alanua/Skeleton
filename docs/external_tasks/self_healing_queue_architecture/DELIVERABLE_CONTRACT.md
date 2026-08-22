# DELIVERABLE CONTRACT — required response structure

Return one Markdown document using these section headings exactly.

# 1. Executive verdict

Give a 0–10 rating for the current queue architecture as you understand it, the three biggest structural weaknesses, and the target end-state in no more than 15 bullets.

# 2. What should be kept, changed, and removed

Three tables:

- KEEP
- CHANGE
- REMOVE

Be explicit. Name current mechanisms/files/concepts when possible.

# 3. Canonical state model

Define the durable canonical task state machine and the per-attempt/per-route state machine.

Include:

- state names;
- legal transitions;
- transition owner;
- durable evidence required;
- retry/reroute permission;
- terminality;
- GitHub label projection, if any.

Prefer Mermaid diagrams plus a transition table.

# 4. Ownership matrix

For each responsibility, name exactly one authoritative owner:

- queue admission;
- runnable selection;
- dependency waiting/resume;
- task profile derivation;
- route/binding selection;
- route lease;
- attempt execution;
- health update;
- deliverable validation;
- test validation;
- publication;
- retry;
- reroute;
- recovery;
- operator escalation;
- final DONE;
- GitHub label projection;
- notifications.

Identify any current split-brain ownership that should be removed.

# 5. Failure taxonomy and recovery matrix

Provide a complete table with at least these columns:

`failure_class | layer | examples | retry_same_attempt? | retry_same_binding? | reroute? | cooldown? | deterministic_repair? | reconciliation_required? | operator_required? | attempt_budget_effect | notes`

Cover all cases in `FAILURE_CASES.md`.

# 6. Self-healing loop

Describe the exact periodic/event-driven control loop.

Required pseudo-flow:

`READ durable state -> RECONCILE expired/ambiguous work -> CLASSIFY failures -> APPLY fixed recovery/reroute policy -> REPLENISH runnable work -> VERIFY progress -> PROJECT GitHub state -> NOTIFY only durable operator conditions`

Specify which steps are event-driven, timer-driven, startup-driven, and completion-driven.

# 7. Durable data model

Propose concrete SQLite entities/tables or equivalent records.

At minimum consider:

- TaskRecord
- DependencyRecord
- SchedulerOccurrence / claim
- ExecutionAttempt
- ExecutionBindingSnapshot
- RouteLease
- DeliverableReceipt / ValidationReceipt
- PublicationReceipt
- RecoveryOccurrence
- HealthRecord / CircuitBreaker
- OperatorGate
- ProjectionCursor / GitHub projection state

For each record give key fields, unique/idempotency keys, retention, and privacy classification.

# 8. Idempotency, leases, heartbeats, and crash recovery

Walk through crash/restart behavior for:

- before attempt start;
- during read-only attempt;
- during repository mutation;
- during PR publication;
- during external/device mutation;
- after side effect but before receipt;
- after receipt but before GitHub label/comment;
- Runner restart;
- host reboot;
- GitHub outage.

State exactly when replay is safe and when it is forbidden.

# 9. Routing and circuit breakers

Define interaction between:

- executor health;
- harness health;
- provider health;
- model capability health;
- task-class/capability-specific evidence;
- privacy/locality;
- budget;
- cooldown;
- retry/reroute attempt budget.

Explain how a failure on one model/executor/capability does not poison unrelated routes.

Explain how to avoid flapping and how a cooled route returns to service.

# 10. Queue fairness and continuous productivity

Design the replenishment policy so unrelated work continues when tasks are waiting on:

- operator approval;
- provider cooldown;
- credential onboarding;
- dependency;
- physical event;
- hardware/device availability;
- validation/publication.

Propose a fairness algorithm across domains/priorities that avoids starvation but preserves P0 urgency.

# 11. GitHub role

Define what GitHub issues/labels/comments/PRs should represent and what they must NOT be authoritative for.

Explain how local durable state and GitHub projection reconcile after outages or stale labels.

# 12. Protected/high-risk flow

Give the exact lifecycle for protected Runner/gate/workflow/secret/deploy/server/finance/legal/governance changes.

Show where automatic work stops and exact-head operator approval begins.

# 13. Observability and SLOs

Give measurable health indicators and target thresholds.

At minimum:

- runnable-to-start latency;
- idle-with-work duration;
- false-DONE rate;
- successful autonomous recovery rate;
- mean recovery time;
- duplicate side-effect rate;
- stale label projection age;
- tasks blocked by dependency/operator/provider;
- per-binding success/no-progress/validation rates;
- queue throughput by domain/priority;
- restart recovery correctness.

Define what 'queue is 9/10' means quantitatively.

# 14. Migration plan

Provide staged implementation phases from current main.

For each phase include:

- objective;
- changed conceptual components/files;
- protected/non-protected status;
- migration risks;
- exact acceptance tests;
- rollback plan;
- whether operator approval is required.

Prioritize the smallest set of changes that removes false DONE and manual queue nudging first.

# 15. Machine-enforced invariants

List invariants that must become tests/gates/assertions rather than documentation.

Include at least:

- DONE requires accepted DeliverableValidation;
- required edit + zero changed files != DONE;
- exact-head validation only;
- protected accepted change => NEEDS_OPERATOR;
- privacy denial never reroutes to cloud;
- ambiguous mutation never blind-retries;
- hidden provider/executor fallback forbidden;
- task prose cannot select authority fields;
- labels cannot override canonical durable state;
- blocked task cannot prevent unrelated replenishment;
- retry/reroute budgets are finite.

# 16. Pseudocode / interfaces

Provide concise pseudo-APIs for the core controller, for example:

- `reconcile_queue()`
- `classify_attempt_failure()`
- `eligible_bindings()`
- `acquire_route_lease()`
- `finalize_attempt()`
- `validate_deliverable()`
- `schedule_recovery()`
- `resume_dependency_waiters()`
- `replenish_queue()`
- `project_github_state()`

Show where transactions begin/end.

# 17. Test matrix

Give a deterministic test matrix covering all `FAILURE_CASES.md`, including restart/fault-injection tests and property/invariant tests.

# 18. Critical review of Skeleton's current approach

Be adversarial. Identify architectural debt, duplicated authority, misleading abstractions, unsafe compatibility layers, and mechanisms that should be deleted once migration completes.

# 19. Recommended target architecture in one page

End with a compact one-page reference architecture: components, durable stores, flows, gates, failure handling, and operator boundary.

# 20. Top 10 implementation actions

Number them in exact execution order. Each action must be concrete enough to turn into a GitHub issue.

## Output rules

- Do not include secret values or private runtime paths.
- Do not recommend weakening operator/protected/privacy gates.
- Distinguish facts from recommendations.
- If you think an existing accepted architecture decision is wrong, say so and explain the replacement precisely.
- Prefer deleting ambiguous compatibility behavior over layering more fallback logic on top.
- Do not write production code unless explicitly asked later; this deliverable is architecture only.
