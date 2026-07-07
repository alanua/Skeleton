# n8n Community Self-Hosted Package

This package defines a conservative SQLite-based n8n Community deployment for private self-hosting. It is intentionally local-first: the editor binds to `127.0.0.1` by default, no public webhook route is configured, and the compose file contains one application service only.

## Image Pin

The reviewed image is pinned as:

`n8nio/n8n:1.97.1@sha256:4c466c8d7a0f21fb498fb019b6f8d6f2dc9c90524859aa0f5d1b3feec8e8d30d`

The pin must be refreshed only through a reviewed change that records the exact upstream tag and digest evidence. Do not switch to a floating tag.

## Files

- `deploy/n8n/compose.yaml`: one-service Docker Compose definition.
- `deploy/n8n/env.example`: descriptors for an uncommitted env file.
- `deploy/n8n/validate.sh`: static and local safety validation.
- `deploy/n8n/backup.sh`: quiesced SQLite backup that captures `database.sqlite` with WAL/SHM files when present.
- `deploy/n8n/restore.sh`: isolated archive validation and restore.
- `deploy/n8n/rollback.sh`: wrapper for restore from a known-good archive.

## Private Access

Keep `N8N_EDITOR_PORT=5678` and the default loopback bind. Use one private access path:

- SSH tunnel: `ssh -L 5678:127.0.0.1:5678 <host>`
- Tailscale SSH: connect to the host privately, then browse `http://127.0.0.1:5678` through the tunnel/session.

Do not expose the editor or webhooks through a public reverse proxy without a separate reviewed design.

## Data And Secrets

`N8N_DATA_DIR` must be a dedicated owner-only directory mounted to `/home/node/.n8n`. The SQLite database path is `/home/node/.n8n/database.sqlite`; this package must never point at Skeleton private-memory SQLite files.

`N8N_ENCRYPTION_KEY` is a private descriptor in `env.example`. Generate and store the real value only in an uncommitted env file. Do not commit `.env`, credentials, active workflows, exported credentials, or community-node packages.

## SQLite Operations

`DB_SQLITE_POOL_SIZE=2` is explicit and nonzero so n8n enables WAL mode. Backups stop the n8n service before copying SQLite state, then archive `database.sqlite` and the WAL/SHM companions when they exist. Restores validate the archive shape in a temporary directory before replacing the database files.

## Migration Triggers

Stay on SQLite while this remains a small private deployment. Plan a reviewed SQLite-to-PostgreSQL migration before any of these become true:

- sustained concurrent editor or workflow usage;
- frequent queue pressure or long-running executions;
- database size growth that makes quiesced backups operationally costly;
- need for high availability, external workers, or separate execution scaling;
- recurring SQLite lock contention, slow vacuum/checkpoint work, or restore windows that exceed acceptable downtime.

This package documents migration triggers only. It does not add PostgreSQL, Redis, queue mode, workers, or public webhook routing.
