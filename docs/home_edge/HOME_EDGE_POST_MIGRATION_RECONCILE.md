# Home Edge Post Migration Reconcile

`home_edge_01_post_migration_reconcile_v1` is a fixed-purpose audited runtime-maintenance task for bounded post-Debian-migration drift repair on `home-edge-01`.

The task accepts only these non-empty runtime fields:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: home_edge_01_post_migration_reconcile_v1
Repository: alanua/Skeleton
Expected Main SHA: <40 lowercase hex main SHA>
Operator Approval: EXPLICIT_RECONCILE_HOME_EDGE_POST_MIGRATION_20260808
Target: home-edge-01
```

Any other non-empty field is rejected before a Home Edge request is built. Issue text never controls argv, command text, paths, services, hosts, script fragments, run identity, lane, or timeout.

The runner verifies the expected main SHA against the registered local `main` commit and `origin/main`, then sends one signed `HomeEdgeExecRequest` through `core.home_edge.executor_gateway.execute_home_edge_request`. The mutation request is bound to `operator_approval_ref=EXPLICIT_RECONCILE_HOME_EDGE_POST_MIGRATION_20260808`, `node_id=home-edge-01`, `run_as=root`, `execution_lane=privileged_mutation`, a 900 second timeout, and idempotency key `home-edge-01-post-migration-reconcile-20260808-v1`. The module does not provide direct SSH, subprocess transport, credentials, or arbitrary command execution.

The node script fails closed unless Debian 13, hostname `home-edge-01`, user `oleksii` UID 1000 with home `/home/oleksii`, the signed gateway path, boot identity, and `skeleton-devices doctor` are present and healthy before mutation.

Repairs are bounded to existing Home Edge runtime state:

- Screensaver gallery refresh uses `/home/oleksii/.local/bin/home-edge-screensaver-gallery-refresh` and verifies with `/home/oleksii/.local/bin/home-edge-screensaver-verify-v9`.
- The 47-vs-prior-48 gallery discrepancy is classified as unavailable upstream asset, stale cache/state, duplicate identity, broken asset/metadata, or verifier assumption. The script preserves valid cached art and does not lower integrity or qualification criteria.
- Aggregate platform verifier repair is an exact fail-closed transformation to use the current Brother ScanKey v4 verifier. It does not remove, bypass, or weaken Brother checks.
- Stale `/home/valertos08` replacement scans only closed, non-secret Skeleton runtime/config boundaries and replaces exact path prefixes only where the canonical `/home/oleksii` target exists.
- Every touched file is backed up in a private restrictive rollback bundle with a deterministic manifest before mutation.

Postchecks require healthy screensaver verifier, Brother ScanKey v4 verifier, aggregate platform verifier, device registry doctor, Skeleton Cast, pointer broker service/socket/uinput, media watchdog with zero critical and warnings, zero failed units attributable to the operation, zero obsolete bounded stale home-path matches, unchanged boot id, no reboot, and a readable retained rollback bundle.

The public receipt is aggregate-only: allowlisted counts, booleans, stable reason classes, and hashes. Private runtime details stay on `home-edge-01` in the executor audit log and private rollback/log directory.
