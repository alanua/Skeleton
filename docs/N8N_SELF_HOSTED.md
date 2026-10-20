# n8n Community Self-Hosted Package

This package defines a conservative SQLite-based n8n Community deployment for private self-hosting. It is local-first: the editor binds to `127.0.0.1`, no public webhook route is configured, and the Compose file contains one application service only.

## Image Pin

The package is pinned to:

```text
n8nio/n8n:2.29.7@sha256:e0264b531fb97c68ece58a650173bd981f1663947281013f4a46749c15a8abc5
```

Do not switch to a floating tag. The source review confirms the n8n `2.29.7` configuration and node contracts described below; the exact registry manifest digest must still be independently checked against the registry before merge or deployment.

## n8n 2.29.7 Security Contract

The settings below were verified against the official `n8n-io/n8n` source tag `n8n@2.29.7`:

- `packages/@n8n/config/src/configs/runners.config.ts` defines `N8N_RUNNERS_MODE=internal` and `N8N_RUNNERS_INSECURE_MODE=false`;
- `packages/cli/src/deprecation/deprecation.service.ts` marks `N8N_RUNNERS_ENABLED` as deprecated and safe to remove, so this package forbids that obsolete variable;
- `packages/workflow/src/workflow-data-proxy-env-provider.ts` confirms that `N8N_BLOCK_ENV_ACCESS_IN_NODE=true` blocks workflow access to process environment variables;
- `packages/@n8n/config/src/configs/security.config.ts` defines `N8N_RESTRICT_FILE_ACCESS_TO` and `N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES`;
- `packages/@n8n/config/src/configs/nodes.config.ts` defines `NODES_EXCLUDE` as a JSON array of node type identifiers.

The package uses one internal runner with bounded concurrency and timeout. It does not configure an external runner, broker credential, Redis, queue mode, or Docker socket.

The following n8n `2.29.7` node type identifiers are excluded:

```text
n8n-nodes-base.code
n8n-nodes-base.executeCommand
n8n-nodes-base.ssh
n8n-nodes-base.localFileTrigger
n8n-nodes-base.readWriteFile
n8n-nodes-base.readBinaryFile
n8n-nodes-base.readBinaryFiles
n8n-nodes-base.writeBinaryFile
```

Python execution, community packages, workflow environment access, and access to n8n-owned files are disabled. The allowed file-access path is an intentionally unavailable container path.

## Files

- `deploy/n8n/compose.yaml`: one-service SQLite Compose definition with localhost-only binding, read-only root filesystem, bounded tmpfs/resources, internal runner restrictions, and dangerous-node exclusions.
- `deploy/n8n/env.example`: descriptors for an uncommitted owner-only environment file.
- `deploy/n8n/validate.sh`: fail-closed environment, path, ownership, image, Compose, runner, and node-security validation.
- `deploy/n8n/backup.sh`: quiesced SQLite backup with service-state recovery and a non-secret manifest.
- `deploy/n8n/restore.sh`: isolated archive, sidecar, manifest, config-hash, key-fingerprint, SQLite-integrity, health, and rollback validation.
- `deploy/n8n/rollback.sh`: invokes the same validated restore state machine.

## Private Access

Keep `N8N_EDITOR_PORT=5678` and the default loopback bind. Use one private access path:

- SSH tunnel: `ssh -L 5678:127.0.0.1:5678 <host>`
- Tailscale SSH: connect to the host privately, then browse `http://127.0.0.1:5678` through the tunnel/session.

Do not expose the editor or webhooks through a public reverse proxy without a separate reviewed design.

## Data And Secrets

`N8N_DATA_DIR` must be a dedicated owner-only directory mounted to `/home/node/.n8n`. `N8N_BACKUP_DIR` must also be owner-only and non-overlapping. Both directories must resolve to canonical absolute paths, be owned by UID/GID 1000, and have mode `700`. The env file must be a regular non-symlink owner-only file, normally mode `600`, with unique exact `KEY=VALUE` entries.

The SQLite database path is `/home/node/.n8n/database.sqlite`; this package must never point at Skeleton private-memory SQLite files.

`N8N_ENCRYPTION_KEY` exists only as a descriptor in `env.example`. Generate and store the real value only in an uncommitted environment file. It must be at least 32 characters. Scripts never print it; backup manifests store only a SHA-256 fingerprint so restores reject archives made with a different key.

Do not commit `.env`, credentials, active workflows, exported credentials, customer data, private paths, or community-node packages.

## Backup Contract

`DB_SQLITE_POOL_SIZE=2` is explicit and nonzero so n8n enables WAL mode. A backup records whether n8n was running, installs signal/error recovery traps before stopping it, and returns the service to its prior running or stopped state. A previously running service must become Docker-health `healthy` within a bounded timeout before the archive is published.

The archive contains only regular root entries:

- `database.sqlite`;
- optional `database.sqlite-wal`;
- optional `database.sqlite-shm`;
- `manifest.sha256`.

The manifest contains the exact image pin, active Compose SHA-256, redacted environment SHA-256, encryption-key fingerprint, and SQLite file hashes. The checksum sidecar is owner-only, non-symlink, single-line, and names the exact archive.

## Restore And Rollback Contract

Restore and rollback reject path traversal, links, devices, duplicate archive members, unexpected members, missing members, malformed or duplicate manifest keys, malformed hashes, unsafe sidecars, image mismatch, active Compose/environment hash mismatch, key mismatch, file-hash mismatch, and failed SQLite `PRAGMA integrity_check`.

The current database is snapshotted after service stop and before replacement. Staged `.new` files are removed on every failure path. Once replacement begins, any error or signal restores the emergency snapshot. If the service was previously running, the replacement is accepted only after bounded Docker-health status `healthy`; unhealthy, exited, dead, or timeout results trigger database rollback and restoration of the prior service state. A previously stopped service remains stopped.

## Validation

Focused synthetic validation uses only temporary directories, synthetic SQLite databases, and a fake Docker executable:

```bash
pytest -q tests/test_n8n_deployment_package.py
bash -n deploy/n8n/backup.sh deploy/n8n/restore.sh deploy/n8n/validate.sh deploy/n8n/rollback.sh
git diff --check
```

Before merge, also run the repository-wide pytest profile with all `SKELETON_HOME_EDGE_01_*` variables removed from the child environment. Before deployment, independently verify the image digest and run bounded `docker compose config` and health checks on a non-production host.

## Migration Triggers

Stay on SQLite while this remains a small private deployment. Plan a reviewed SQLite-to-PostgreSQL migration before any of these become true:

- sustained concurrent editor or workflow usage;
- frequent queue pressure or long-running executions;
- database size growth that makes quiesced backups operationally costly;
- need for high availability, external workers, or separate execution scaling;
- recurring SQLite lock contention, slow vacuum/checkpoint work, or restore windows that exceed acceptable downtime.

This package documents migration triggers only. It does not add PostgreSQL, Redis, queue mode, workers, or public webhook routing.
