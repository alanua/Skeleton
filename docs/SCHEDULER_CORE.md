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

Production launch uses the user-level runtime installer from the repository root:

```bash
python3 scripts/install_scheduler_runtime.py --expected-sha "$(git rev-parse HEAD)" --enable
```

The installer verifies the exact clean source SHA and the canonical `alanua/Skeleton` origin before writing runtime files. It creates an immutable release under `~/.local/share/skeleton/scheduler/releases/<sha>` and promotes it through the atomic `~/.local/share/skeleton/scheduler/current` symlink.

Private scheduler state lives under `~/.local/state/skeleton/scheduler`; the live database is `scheduler.sqlite3`. The fixed user units are `~/.config/systemd/user/skeleton-scheduler.service` and `~/.config/systemd/user/skeleton-scheduler.timer`.

The service is `oneshot` and runs the fixed Python interpreter, immutable release CLI path and live DB path. No schedule, issue body, workflow input or environment variable can choose a command, script path or database authority. The timer is the single Scheduler Core timer, runs every 60 seconds, and is persistent.

The installer initializes the live DB, requires the public-safe live status to be `READY`, and runs an isolated synthetic smoke against a temporary database. That smoke registers a due `notify_only` once schedule, verifies the first tick creates one `done` occurrence, verifies the second tick creates zero duplicates, verifies the occurrence count is one, and removes the temporary state before returning.

The public installer receipt is aggregate-only: source SHA, runtime/service/timer/live statuses, synthetic smoke counts, rollback readiness and stable reason codes. It contains no paths, schedule payloads, rows, private values or host details.

## Protected runtime launch

The reviewed `.github/workflows/scheduler-runtime-launch.yml` is the only automated production launch route for v1. It runs on the registered Hetzner self-hosted runner from an exact clean `main` checkout, validates scheduler contracts, invokes the fixed user-level installer, and verifies exactly one enabled and active user timer through `systemctl --user`.

No synthetic schedule is written to the live scheduler database. GitHub receives only an aggregate DONE/BLOCKED receipt without schedule payloads, rows, private paths or host values.

The authoritative aggregate production launch receipt is recorded in GitHub issue `#2051`.

## Boundaries

- no direct canonical memory SQLite access;
- no arbitrary shell, SQL or Python from schedule payloads;
- no authority from Calendar or Telegram text;
- no second Runner;
- no automatic protected, finance, legal, deployment or secret action;
- no private payloads in public receipts or GitHub evidence.
