# Autonomous Review Gate

Draft PR publication schedules one canonical runner control route:
`runner/internal_review_control`. The schedule payload is a bounded public-safe
request with repository, PR number, exact head SHA, source issue, allowed files,
and requested scope. It does not carry approved capabilities.

`scripts/scheduler_runtime.py::run_scheduler_tick` builds
`SharedDispatcher.for_loop_engine(...)` with the scheduler DB path and the
production PR state reader. A tick claims the pending review occurrence and the
dispatcher re-reads the current PR, head, changed files, validation status,
mergeability, scope, privacy, and protected-file policy before producing a typed
verdict.

Verdicts materialize durable control state:

- `APPROVE` creates the existing authorized continuation only for unprotected
  current-head PRs.
- `REQUEST_CHANGES` creates or reuses one bounded repair control item tied to
  repository, PR, head SHA, and policy reason. Repair completion schedules or
  reuses a re-review.
- `DO_NOT_MERGE` stays internal for repair, supersede, or dependency handling
  and does not notify the operator.
- `NEEDS_OPERATOR` records an exact packet with repository, PR, head SHA,
  permitted merge method, policy reason, and next continuation, then claims one
  durable operator notification.

Unknown runner routes remain fail-closed at the shared dispatch boundary. The
scheduler may request capabilities in payload metadata, but only dispatcher
route grants backed by Runner/gate/registry authority satisfy them.
