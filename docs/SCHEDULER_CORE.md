# Scheduler Core v1

Scheduler Core is Skeleton's single local authority for delayed and recurring occurrence generation. It is not a second Runner and does not execute arbitrary commands.

## Runtime flow

```text
Schedule Registry
→ due-time resolver
→ Occurrence Ledger
→ typed execution proposal
→ dispatcher or operator review (later integration)
```

The v1 dispatcher boundary is deliberately inactive. A tick may produce a private typed proposal, but it does not enqueue Runner, start Loop, call a network provider, write canonical memory, or execute shell commands.

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
needs_operator → pending | skipped | failed | done
```

A stale `running` occurrence is never silently repeated. Restart recovery moves it to `needs_operator`.

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

## Boundaries

- no direct canonical memory SQLite access;
- no arbitrary shell, SQL or Python from schedule payloads;
- no authority from Calendar or Telegram text;
- no second Runner;
- no automatic protected, finance, legal, deployment or secret action;
- no private payloads in public receipts or GitHub evidence.
