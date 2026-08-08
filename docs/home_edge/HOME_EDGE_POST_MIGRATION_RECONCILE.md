# Home Edge Post Migration Reconcile

`home_edge_01_post_migration_reconcile_v1` is a fixed-purpose runtime maintenance task for `home-edge-01`. It is only reachable through the existing signed Home Edge executor gateway:

- `core.home_edge.executor.sign_request`
- `core.home_edge.executor_gateway.execute_home_edge_request`

The runner accepts only this issue metadata before constructing the signed request:

- `Mode`
- `Maintenance Task ID`
- `Repository`
- `Expected Main SHA`
- `Operator Approval`
- `Target`

Unknown non-empty fields, duplicate fields, malformed SHA values, repository mismatches, target mismatches, or approval mismatches block before any runtime request is built.

## Runtime Contract

The request is pinned to:

- task ID: `home_edge_01_post_migration_reconcile_v1`
- repository: `alanua/Skeleton`
- target: `home-edge-01`
- approval: `EXPLICIT_RECONCILE_HOME_EDGE_POST_MIGRATION_20260808`
- lane: `privileged_mutation`
- run as: `root`
- timeout: `900`
- idempotency key: `home-edge-01-post-migration-reconcile-20260808-v2`

The root script uses a fixed desktop-user helper for user-session commands:

```text
runuser -u oleksii -- env HOME=/home/oleksii XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus ...
```

Issue text cannot change commands, paths, users, units, environment, lane, timeout, or script content.

## Fixed Entrypoints

The task uses only the verified live entrypoints:

- `/home/oleksii/.local/bin/home-edge-screensaver-gallery-refresh`
- `/home/oleksii/.local/bin/home-edge-screensaver-verify-v9`
- `/home/oleksii/.local/bin/brother-scan-key-verify`
- `/home/oleksii/.local/bin/home-edge-platform-repair-verify`
- `/home/oleksii/.local/bin/skeleton-devices`
- `/home/oleksii/.local/bin/skeleton-cast-control status`
- `/home/oleksii/.local/bin/home-edge-pointer-status`
- `/home/oleksii/.local/bin/home-edge-media-watchdog status`

It intentionally does not use invented aliases such as `home-edge-brother-scankey-verify-v4`, `home-edge-platform-verify`, or `home-edge-media-watchdog-status`.

## Reconcile Scope

The task performs these bounded operations:

- verify Debian 13, hostname `home-edge-01`, user `oleksii`, UID `1000`, home `/home/oleksii`, and unchanged boot ID
- run `skeleton-devices doctor` as the desktop user before mutation
- run the screensaver verifier with its default no-argument contract; refresh once only if the precheck fails
- require the specialized Brother verifier to be healthy
- repair the aggregate verifier only when it is a regular bounded file containing exactly one `brother_guard_v2.service` literal and the replacement user unit `brother-guard.service` exists and is active
- replace only that exact Brother unit literal
- scan and repair only bounded operational stale-home references, preserving owner/mode and backing up each edited file
- run systemd daemon reload only for matching edited unit-file managers, without service restarts
- verify registry, screensaver, Brother, aggregate, Cast, pointer, watchdog, failed system/user unit counts, stale operational match count, boot ID, and rollback readability

The stale-path scan excludes private and historical locations, including memory-gate data, device-registry history or database files, phone SSH, GitHub app data, Gmail data, secrets, credentials, browser profiles, documents, archives, backups, SSH keys, `known_hosts`, and key/token/password files.

## Receipt Boundary

The public receipt is constrained by `schemas/home_edge_post_migration_reconcile_receipt.schema.json`. It contains only allowlisted counts, booleans, stable reason classes, hashes, and statuses.

This task does not claim `/var/lib/skeleton` JSON as MemoryGate persistence and does not create a second persistence path. Canonical memory persistence is intentionally performed after successful runtime execution by the reviewed `home_edge_audit_persist_v1` Runner maintenance task, using the sanitized runtime-audit-compatible receipt emitted here.
