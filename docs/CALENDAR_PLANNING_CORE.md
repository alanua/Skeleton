# Calendar Planning Core and Travel composition

This slice connects the existing provider-independent Scheduler Core to Travel through typed workflow proposals. It does not call Google Calendar or any other provider and it performs no external mutation.

## Authority model

Calendar authority is event-specific:

- `SKELETON_OWNED` — Skeleton owns the projection, but protected manual drift still requires review.
- `EXTERNAL_OWNED` — the external calendar is authoritative for the field.
- `SHARED` — both systems may write; conflicting drift requires review.
- `USER_OVERRIDDEN` — automatic reconciliation stops until review.

Field ownership is recorded separately for time, title, description, location, attendees and reminders. External events remain authoritative facts; Skeleton-owned Travel projections remain derived from the private Travel plan.

## Core records

`CalendarBinding` links one opaque Travel subject to one projection role:

```text
trip_ref + projection_role
→ stable binding_id
→ opaque calendar_ref
→ optional remote_event_ref
→ source/projected/remote revisions
→ projected field hashes
→ field ownership
```

Projection roles in the first slice:

- `travel_primary` — primary event in the Travel calendar;
- `family` — optional family-calendar projection;
- `work_absence` — optional work-absence proposal.

Provider IDs and private event content are never placed in public receipts. Private event content is referenced by `private_payload_ref` and compared using deterministic field hashes.

## Reconciliation

The reconciler returns a private mutation proposal with one action:

- `CREATE`
- `UPDATE`
- `CANCEL`
- `NOOP`
- `NEEDS_OPERATOR`

It never executes the proposal. A later provider adapter and ActionGate must validate and perform approved mutations.

Automatic operations are limited to safe Skeleton-owned projection changes, such as description, location and reminder updates. Review is required for:

- work-calendar blocking;
- invitations or attendee changes;
- manual time drift;
- shared/external/user-overridden field conflicts;
- external deletion or cancellation;
- cancellation of a confirmed trip.

Completed trips are archived without deleting their calendar history. Cancelled candidate projections may produce a cancellation proposal; confirmed-trip cancellation requires operator approval.

## Availability and conflicts

Busy intervals from all authorized calendars are normalized into one merged timeline. The shared core can:

- find conflicts for a proposed trip interval;
- ignore the projection being reconciled;
- calculate free windows above a minimum duration.

The provider adapter remains responsible for acquiring authorized calendar snapshots. This shared core receives only normalized opaque intervals.

## Scheduler composition

Scheduler Core remains responsible only for **when** a typed operation becomes due. Travel remains responsible for the meaning and policy of the operation.

Candidate trips receive:

- `travel.calendar_reconcile` daily;
- `travel.trip_review_due` weekly;
- `travel.price_check_due` weekly.

Planned trips receive daily reconciliation, a weekly price check and future one-time `T-30`, `T-7`, itinerary refresh and departure-preflight schedules when those times have not passed.

Trips in progress receive daily reconciliation and itinerary refresh. Completed and cancelled trips produce no future schedules.

Scheduler payloads contain only an opaque `trip_ref`, source revision, projection roles and typed operation. They contain no Google IDs, event titles, addresses, routes, credentials, booking data or private payload references.

## Correct execution boundary

```text
Scheduler Core
→ typed Travel workflow proposal
→ Travel planning policy
→ Calendar reconciliation proposal
→ ActionGate
→ provider adapter
→ approved calendar mutation
```

Condition monitoring such as prices, weather or transport remains a Travel workflow. Scheduler only decides when that workflow runs.

## Not included

- Google Calendar API calls;
- OAuth or credentials;
- live calendar reads or writes;
- operation registry or protected gate changes;
- deployment or worker activation;
- booking, payment or invitation sending;
- MemoryGateway mutation.

A separate protected follow-up must connect these contracts to the calendar adapter, run private dry-run reconciliation, verify drift behavior, and request exact operator approval before live writes.
