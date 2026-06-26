# Read-Only Audit Validation

This repository includes a GitHub-hosted validation path for reviewers who need
public, repeatable test evidence without access to the live Runner checkout,
secrets, private data, or a permanent writable mount.

The workflow is `.github/workflows/readonly-audit-validation.yml`. It runs only
for `pull_request` and manual `workflow_dispatch` events. It uses `contents:
read`, checks out the exact event SHA with credentials disabled, and executes a
fixed in-repository script. It never uses `pull_request_target`.

## Snapshot Model

`scripts/readonly_audit_snapshot.py` creates a source snapshot from tracked files
at the verified checked-out SHA. The snapshot excludes `.git`, `.env` files,
`secrets/**`, `private/**`, `runtime/**`, cache/build outputs, local databases,
and symlinks. The resulting `audit_manifest.json` records the repository, SHA,
file count, deterministic SHA-256 hashes, exclusions, Python version, and
platform. The source snapshot is made read-only before validation begins.

Validation never writes into the read-only source snapshot. Each profile runs in
a temporary writable copy with its own virtual environment. The copy and virtual
environment are deleted after success or failure. Cleanup failure blocks success.

## Profiles

`core` always runs:

- project Python compile check;
- isolated install of `.[dev]`;
- `python -m pytest -q`;
- `git diff --check`.

`aufmass_geometry` runs only when the `aufmass-geometry` optional dependency
group exists. It installs `.[dev,aufmass-geometry]`, runs the geometry and DXF
tests, then runs the geometry healthcheck and synthetic benchmark. The profile
fails if geometry or DXF tests are skipped.

The workflow does not consume issue-provided commands, paths, package names,
versions, or profiles. Supported profiles are fixed by the script.

## Public Evidence

The workflow publishes GitHub check status and bounded logs, then uploads
short-retention artifacts:

- `audit_manifest.json`;
- `validation_summary.json`.

The summary contains the exact SHA, profile status, pass/fail/skip counts,
duration class, cleanup status, and success criteria. It sanitizes token-like
values, absolute host paths, and environment-assignment lines from public log
excerpts. It does not upload virtual environments, package caches, credentials,
environment dumps, private data, or unrestricted command output.
