# Autonomous Review Gate

Draft PR publication schedules one canonical runner control route:
`runner/internal_review_control`. The schedule payload is a bounded public-safe
request with repository, PR number, exact head SHA, source issue, allowed files,
and requested scope. It does not carry approved capabilities.

`scripts/scheduler_runtime.py::run_scheduler_tick` builds
`SharedDispatcher.for_loop_engine(...)` with the scheduler DB path and the
production PR state reader plus concrete default review-control adapters. A tick
claims the pending review occurrence and the dispatcher re-reads the current PR,
head, changed files, validation status, mergeability, scope, privacy, and
protected-file policy before producing a typed verdict.

Verdicts materialize durable control state:

- `APPROVE` carries the delegated merge-policy authority that produced it. When
  the PR head is still exact/current, the policy verdict is `AUTO_MERGE_ALLOWED`,
  and the changed-file scope is non-protected, it materializes an automatic
  authorized workflow continuation. That continuation does not create
  `user_approved`, trusted approval references, protected approval references,
  operator evidence, or protected authority. Protected, stale/moved,
  operator-required, never-auto, privacy/security, and genuine external-approval
  cases fail closed to `NEEDS_OPERATOR` or `DO_NOT_MERGE`.
- `REQUEST_CHANGES` creates or reuses one public-safe `agent:task` repair issue
  tied to the reviewed PR/head, source issue, allowed files, policy reason, and
  deterministic idempotency key. The repair task starts from current `main`,
  rereads the exact reviewed PR/head/diff, preserves useful changes, fixes only
  review findings, and publishes one replacement draft PR that supersedes the
  reviewed candidate. Verified repair completion and re-review are recorded only
  after replacement PR publication succeeds.
- `DO_NOT_MERGE` stays internal for repair, supersede, or dependency handling
  and does not notify the operator.
- `NEEDS_OPERATOR` records an exact packet with repository, PR, head SHA,
  permitted merge method, policy reason, and next continuation, then claims one
  durable operator notification.

Unknown runner routes remain fail-closed at the shared dispatch boundary. The
scheduler may request capabilities in payload metadata, but only dispatcher
route grants backed by Runner/gate/registry authority satisfy them.
