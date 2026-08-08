# Home Edge Post Migration Reconcile

`home_edge_01_post_migration_reconcile_v1` is a fixed runtime maintenance task for
`home-edge-01` after the Debian 13 migration. It is dispatched only through
`core.home_edge.executor_gateway.execute_home_edge_request` with a signed
`HomeEdgeExecRequest`.

The issue body may provide only these non-empty metadata fields:

- `Mode`
- `Maintenance Task ID`
- `Repository`
- `Expected Main SHA`
- `Operator Approval`
- `Target`

All command paths, unit names, environment, user identity, timeout, lane, and script
content are source-owned constants. The request uses the privileged mutation lane,
runs as root, and embeds one bash script. The script uses the fixed desktop session
wrapper for user helpers:

```text
runuser -u oleksii -- env HOME=/home/oleksii XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus ...
```

## Scope

The script verifies Debian 13, hostname `home-edge-01`, desktop user `oleksii`,
UID `1000`, home `/home/oleksii`, `/run/user/1000`, the user D-Bus socket, and a
stable boot ID. It validates fixed Home Edge helper executables before any owned
file mutation.

The screensaver verifier is run with no arguments. If it is already healthy,
refresh is skipped. If degraded, the fixed gallery refresh helper runs once and
the no-argument verifier must pass afterward.

Brother health is checked with the fixed verifier JSON, the current user service
`brother-scan-key.service`, and guard timer `brother-scan-key-guard.timer`.
Aggregate verification can repair only the exact stale literal
`brother_guard_v2.service` when it appears exactly once in the aggregate verifier
source, replacing it with `brother-scan-key.service`.

Stale home repair is limited to current operational files under:

- `/home/oleksii/.local/bin`
- `/home/oleksii/.config/systemd/user`
- `/etc/systemd/system`

Only path tokens beginning `/home/valertos08` are eligible, and each mapped
`/home/oleksii` target must exist before replacement. Enumeration is NUL-safe.
Backups and a deterministic manifest are retained in a private root-owned rollback
directory. Later failures restore changed files and reload affected systemd
managers before the rollback is considered applied.

## Receipt

The public receipt is intentionally aggregate-only: counts, booleans, stable
status enums, and hashes. It does not include raw logs, paths, environment,
credentials, or private data. It also does not claim direct MemoryGate persistence.
Successful runtime execution reports:

```text
canonical_memory_post_step=home_edge_audit_persist_v1
```

The separate reviewed audit persist maintenance task is responsible for canonical
memory persistence after the runtime receipt has been reviewed.
