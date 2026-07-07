# n8n Community Self-Hosted Package

This package defines a conservative SQLite-based n8n Community deployment for private self-hosting. It is intentionally local-first: the editor binds to `127.0.0.1` by default, no public webhook route is configured, and the compose file contains one application service only.

## Image Pin

The reviewed image is pinned as:

`n8nio/n8n:2.29.7@sha256:e0264b531fb97c68ece58a650173bd981f1663947281013f4a46749c15a8abc5`

The pin must be refreshed only through a reviewed change that records the exact upstream tag and digest evidence. Do not switch to a floating tag.

## Files

- `deploy/n8n/compose.yaml`: one-service Docker Compose definition with a read-only root filesystem and bounded tmpfs mounts.
- `deploy/n8n/env.example`: descriptors for an uncommitted env file.
- `deploy/n8n/validate.sh`: static and local safety validation.
- `deploy/n8n/backup.sh`: quiesced SQLite backup with a non-secret manifest.
- `deploy/n8n/restore.sh`: isolated archive, manifest, key-fingerprint, checksum, and SQLite integrity validation before replacement.
- `deploy/n8n/rollback.sh`: validated rollback operation using the same archive safety checks as restore.

## Private Access

Keep `N8N_EDITOR_PORT=5678` and the default loopback bind. Use one private access path:

- SSH tunnel: `ssh -L 5678:127.0.0.1:5678 <host>`
- Tailscale SSH: connect to the host privately, then browse `http://127.0.0.1:5678` through the tunnel/session.

Do not expose the editor or webhooks through a public reverse proxy without a separate reviewed design.

## Data And Secrets

`N8N_DATA_DIR` must be a dedicated owner-only directory mounted to `/home/node/.n8n`. `N8N_BACKUP_DIR` must also be owner-only and non-overlapping. Both directories must resolve to canonical absolute paths, be owned by UID/GID 1000, and have mode `700`. The env file must be owner-only, normally mode `600`.

The SQLite database path is `/home/node/.n8n/database.sqlite`; this package must never point at Skeleton private-memory SQLite files.

`N8N_ENCRYPTION_KEY` is a private descriptor in `env.example`. Generate and store the real value only in an uncommitted env file. It must be at least 32 characters. The scripts never print it; backup manifests store only a SHA-256 fingerprint of the key so restores can reject archives made with a different key.

Do not commit `.env`, credentials, active workflows, exported credentials, or community-node packages.

## SQLite Operations

`DB_SQLITE_POOL_SIZE=2` is explicit and nonzero so n8n enables WAL mode. Backups preserve whether n8n was running before the operation: a running service is stopped and restarted, and a stopped service is left stopped. The archive contains only these regular root entries: `database.sqlite`, optional `database.sqlite-wal`, optional `database.sqlite-shm`, and `manifest.sha256`.

Restores and rollbacks validate the archive shape, archive checksum data in the manifest, matching key fingerprint, and SQLite `PRAGMA integrity_check` before live replacement. Restored files are staged with UID/GID 1000 and mode `600`. The current database is kept as an emergency rollback point and is restored automatically if replacement or startup validation fails. Replacement is atomic as far as the local filesystem permits.

## Migration Triggers

Stay on SQLite while this remains a small private deployment. Plan a reviewed SQLite-to-PostgreSQL migration before any of these become true:

- sustained concurrent editor or workflow usage;
- frequent queue pressure or long-running executions;
- database size growth that makes quiesced backups operationally costly;
- need for high availability, external workers, or separate execution scaling;
- recurring SQLite lock contention, slow vacuum/checkpoint work, or restore windows that exceed acceptable downtime.

This package documents migration triggers only. It does not add PostgreSQL, Redis, queue mode, workers, or public webhook routing.
