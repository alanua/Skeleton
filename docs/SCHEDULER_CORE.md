# Scheduler Core v1

Scheduler Core is Skeleton's single local authority for delayed and recurring occurrence generation. It is not a second Runner and does not execute arbitrary commands.

## Runtime flow

```text
Schedule Registry
→ due-time resolver
→ Occurrence Ledger
→ typed execution proposal
→ SharedDispatcher or operator review
```

The v1 dispatcher boundary is `core.shared_dispatch.SharedDispatcher`. It is an allowlisted typed dispatch layer for local Scheduler continuation work such as loop-engine packets and control-recovery packets. It is still not a calendar provider, Runner queue, arbitrary shell, network mutation, or canonical memory writer.

Dispatch receipts are keyed by the occurrence attempt idempotency key. Replaying the exact same receipt is a no-op. Reusing the same key with different occurrence, attempt, route result, status, reason or evidence fails closed with `DISPATCH_RECEIPT_IDEMPOTENCY_CONFLICT` instead of hiding the collision.

## Schedule contract

Schedules use `skeleton.schedule.v1`. Supported triggers:

- `once`: one Unix timestamp;
- `cron`: five fields (`minute hour day-of-month month day-of-week`) with numbers, lists, ranges and steps.

Cron resolution uses the configured IANA timezone. Occurrence identity is deterministic over `schedule_id`, immutable schedule version and scheduled Unix timestamp.

Policies:

- approval: `notify_only`, `auto_run_low_risk`, `require_operator_each_occurrence`;
- overlap: `skip`, `queue_one`, `needs_operator`;
- misfire: `run_once`, `skip`, `needs_operator`.

Occurrence states:

```text
pending → running → done | failed | needs_operator
pending → skipped | needs_operator | failed
running → waiting_dependency | pending
waiting_dependency → pending | needs_operator | failed | skipped
needs_operator → pending | skipped | failed | done
```

A stale `running` occurrence is never silently repeated. Restart recovery first checks the latest dispatch receipt for the same occurrence attempt. A completed receipt finalizes the occurrence as `done`; an ambiguous mutating receipt moves it to `needs_operator`; otherwise the occurrence is retried by returning it to `pending` until the bounded attempt limit is exhausted, then it moves to `needs_operator`.

## CLI

```bash
python3 scripts/scheduler_tick.py --db /path/scheduler.sqlite3 register schedule.json
python3 scripts/scheduler_tick.py --db /path/scheduler.sqlite3 tick
python3 scripts/scheduler_tick.py --db /path/scheduler.sqlite3 pause schedule.id
python3 scripts/scheduler_tick.py --db /path/scheduler.sqlite3 resume schedule.id
python3 scripts/scheduler_tick.py --db /path/scheduler.sqlite3 status
```

CLI and systemd output are public-safe aggregate receipts. Schedule payloads and execution proposals stay in the private SQLite database.

## Installation

Run as root from a reviewed checkout:

```bash
scripts/install_scheduler_core.sh
```

Defaults:

- installation: `/opt/skeleton-scheduler`;
- private state: `/var/lib/skeleton/scheduler`;
- service user/group: `agent`;
- one systemd timer tick every 60 seconds.

Environment overrides are available through `SKELETON_SCHEDULER_INSTALL_ROOT`, `SKELETON_SCHEDULER_STATE_ROOT`, `SKELETON_SCHEDULER_USER` and `SKELETON_SCHEDULER_GROUP`.

## Protected runtime launch

The reviewed `.github/workflows/scheduler-runtime-launch.yml` is the only automated production launch route for v1. It runs on the registered Hetzner self-hosted runner from an exact clean `main` checkout, validates scheduler contracts, invokes the fixed installer through non-interactive sudo, verifies exactly one enabled and active timer, and runs an isolated synthetic idempotency smoke.

The smoke database is created under the temporary runner directory and removed before completion. No synthetic schedule is written to the live scheduler database. GitHub receives only an aggregate DONE/BLOCKED receipt without schedule payloads, rows, private paths or host values.

The authoritative aggregate production launch receipt is recorded in GitHub issue `#2051`.

## Boundaries

- no direct canonical memory SQLite access;
- no arbitrary shell, SQL or Python from schedule payloads;
- no authority from Calendar or Telegram text;
- no second Runner;
- no automatic protected, finance, legal, deployment or secret action;
- no private payloads in public receipts or GitHub evidence.
