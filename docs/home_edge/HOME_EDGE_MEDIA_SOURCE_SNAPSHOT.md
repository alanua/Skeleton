# Home Edge Media Source Snapshot

`home_edge_01_media_source_snapshot_v1` is a fixed-purpose Runner runtime-maintenance operation for one read-only capture:

- repository: `alanua/Skeleton`
- target: `home-edge-01`
- public source identity token: `home_edge_01_skeleton_cast_app_py`
- execution lane: `read_only`
- run user: `desktop-user`
- timeout: `30` seconds
- transport: signed `core.home_edge.executor_gateway` request only

The operation is not a general file export facility. Issue metadata may provide only the runtime mode, exact maintenance task ID, repository, expected main SHA, and target. Path, command, script, output path, timeout, lane, user, node, and variant fields are rejected.

## Validation Boundary

Before private publication, the remote executor script checks the fixed source identity and safety:

- the source is a regular, non-symlink, readable file;
- it is not world-writable;
- it is bounded to at most 2 MiB;
- Python parse and compile succeed;
- structural markers identify a Skeleton Cast media source, including Flask, `/video`, a health route, and Skeleton Cast media identifiers;
- likely embedded plaintext credential literals block export.

The Runner independently decodes and revalidates the source from the private executor response, recomputes SHA-256 and byte count, and atomically replaces the latest local snapshot only after the local hash and size match the remote public metadata.

## Private Artifact

The source content is written only under Runner private state, outside the repository, at a fixed relative location:

`home_edge/home_edge_01/media_source_snapshot/app.py.latest`

The directory is mode `0700`; the snapshot file is mode `0600`. The public maintenance report never includes the private artifact path or source content.

Retention is intentionally narrow: keep only the latest private snapshot long enough for the bounded canonicalization follow-up. After canonicalization has consumed the snapshot, remove the private artifact unless an operator explicitly retains it for audit. Do not copy this file into the repository, public issue comments, fixtures, logs, or PR descriptions.

## Public Receipt

The Runner comment exposes only aggregate metadata:

- `maintenance_task_id`
- `source_identity`
- `source_version_marker`
- `source_bytes`
- `source_sha256`
- `python_parse_status`
- `video_route_present`
- `health_route_present`
- `private_artifact_written`
- `private_artifact_hash_matches`
- `executor_receipt_hash`
- `stable_reason`
- `success_criteria`

No source text, route implementation details, media titles/history, private addresses, secrets, local private artifact path, or live LAN values may appear in the public receipt.
