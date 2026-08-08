# Scheduler Autonomous Continuation

This slice closes the proposal-only scheduler gap with one shared dispatch boundary:
`core.shared_dispatch.SharedDispatcher`.

The scheduler remains the time and occurrence authority. It can create due
occurrences, atomically claim `pending` rows as `running`, record public-safe
dispatch receipts, retry or wait according to bounded policy, and create the next
already-authorized deterministic workflow step. It does not grant capabilities
and it does not execute arbitrary commands.

The dispatcher is the only typed boundary for scheduler continuation work. It
accepts only registered `(route_type, route_id)` pairs, validates public-safe
privacy, bounded payload shape, and dispatcher-owned route grants before calling
an existing typed route. Scheduler payloads may request capabilities for scope,
but they cannot approve themselves. The production routes are the existing loop
runner packet path, `loop/loop_engine_packet`, and the canonical internal review
control path, `runner/internal_review_control`.

Runner and executor gates remain side-effect authority. Loop decisions may
produce a next bounded step, but that step is persisted as a new `pending`
occurrence and must be claimed and dispatched again through the same boundary.

Operational state is SQLite-backed and non-canonical. Occurrence lineage,
attempt number, idempotency key, parent receipt, dispatch receipt, and evidence
reference are durable and queryable. Durable learned or project facts remain
outside this store and must continue through MemoryGateway.

Automatic recovery is policy-driven:

- stale `running` attempts return to `pending` while attempts remain;
- recoverable dispatch failures retry up to the configured attempt limit;
- dependency waits move to `waiting_dependency` and resume automatically when
  the dependency occurrence reaches `done`;
- exhausted automatic paths become `needs_operator` with one receipt/evidence
  reason;
- routine done, retry, blocked, and progress states stay internal and do not
  trigger Telegram notification.
