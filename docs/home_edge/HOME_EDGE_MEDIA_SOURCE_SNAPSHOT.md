# Home Edge Media Source Snapshot

`home_edge_01_media_source_snapshot_v1` is a fixed-purpose Runner runtime-maintenance operation for one read-only capture:

- repository: `alanua/Skeleton`
- target: `home-edge-01`
- public source identity token: `home_edge_01_skeleton_cast_app_py`
- execution lane: `read_only`
- run user: `desktop-user`
- timeout: `30` seconds
- signer: exact Runner invocation `/usr/bin/sudo --non-interactive -- /usr/local/bin/skeleton-home-edge-media-source-snapshot-signer --sign`
- transport: signed `core.home_edge.executor_gateway` request only

The operation is not a general file export facility. Issue metadata may provide only the runtime mode, exact maintenance task ID, repository, expected main SHA, target, and exact operator approval `EXPLICIT_MINIMAL_HOME_EDGE_SNAPSHOT_ACCESS_REPAIR_2026_08_09`. Path, command, script, output path, timeout, lane, user, node, and variant fields are rejected. Operator approval is checked before signer activation, credential reads, request signing, or transport.

## Controller Signer Boundary

The trusted controller installer establishes an immutable bootstrap at `/usr/local/lib/skeleton-home-edge-controller/bootstrap/install_home_edge_realtime_controller.sh`. Checkout/worktree installer bytes may be copied and hash-verified only as inert data by the existing protected maintenance fabric; root execution is accepted only from the fixed root-owned `0500` installed bootstrap path. Every runtime payload file copied from the checkout is also pinned by SHA-256 before installation, so mutating or removing the checkout after the inert bootstrap copy cannot alter privileged installer behavior.

The Home Edge node executor installer remains node-only and does not install or activate snapshot signer code. The controller installer copies the reviewed static signer payload and Home Edge request contract into `/usr/local/lib/skeleton-home-edge-controller` and exposes only `/usr/local/bin/skeleton-home-edge-media-source-snapshot-signer --sign`. That wrapper is root-owned, restricted to the canonical Runner service identity `agent`, and invokes the installed library copy with exact `/usr/bin/python3`, `env -i`, and the fixed installed path.

The signer only returns the fixed immutable executor request for this operation. It never opens transport, never executes SSH or MCP, never reads or writes the Runner private artifact, never accepts caller-provided signing material, and rejects any invocation other than `--sign`. Runtime task execution invokes the exact absolute `/usr/bin/sudo` command rather than resolving `sudo` or the signer from `PATH`.

## Executor Authentication

The installed controller signer signs the Home Edge executor request with `SKELETON_HOME_EDGE_EXEC_HMAC_SECRET`. Production Runner code cannot directly read or use that HMAC value under any environment combination. The signer reads only `/etc/skeleton/home-edge-executor-controller.env` and only the single allowlisted variable `SKELETON_HOME_EDGE_EXEC_HMAC_SECRET`.

That fixed private config is parsed as text, never sourced or executed. The resolver never reads or parses `/etc/skeleton/home-edge-01.env`; that file is metadata-only corroboration for the private controller ownership boundary. The fixed `/etc/skeleton` directory plus `/etc/skeleton/home-edge-01.env` and `/etc/skeleton/home-edge-executor-controller.env` are all checked with `lstat`; symlinks are rejected, `/etc/skeleton` must be a directory, both env paths must be regular files, none may be group/world writable, and each env file must be no larger than 64 KiB. The controller env is accepted when its owner is root or the current Runner uid, or when those three fixed paths share one identical owner and group boundary under the same strict checks. The parser accepts only simple `KEY=VALUE` or optional `export KEY=VALUE` entries for the allowlisted variable, ignoring comments, blank lines, and unrelated assignments. Duplicate target entries, NUL bytes, malformed quoted values, shell substitution or variable references, backticks, and multiline continuations fail closed before any executor request is made.

Authentication setup failures are reported only as stable public-safe classes: `executor_auth_config_missing`, `executor_auth_config_unsafe`, or `executor_auth_config_invalid`. The credential value, config path, and variable name are never included in public receipts.

## Validation Boundary

Before any Home Edge request is constructed or signed, the Runner first checks the fixed private artifact location. If a regular, non-symlink, owner-context-safe, private-mode artifact already exists, the Runner reads it with the same 700 KiB bound, reruns UTF-8, credential, Python parse, route, and Skeleton Cast media validation locally, recomputes SHA-256 and byte count, and returns `success_criteria=met` with `stable_reason=already_captured`. That local one-shot path performs zero executor calls and reports only the aggregate `not_required_existing_capture` executor marker.

If the existing artifact is missing, the first capture uses a fresh attempt-scoped executor idempotency key. If transport fails ambiguously before the private artifact is published, the operation fails closed without retrying or writing an artifact; another capture requires a deliberate v2 or newly approved operation rather than unbounded retries.

Before private publication from the first remote capture, the remote executor script checks the fixed source identity and safety:

- the source is `/opt/skeleton/cast/app.py`;
- the source is a regular, non-symlink, readable file;
- it is not world-writable;
- pre-read and post-read `lstat` identity and metadata match;
- it is bounded to at most 700 KiB, which keeps the public JSON plus base64 private response under the universal executor `1_000_000` byte output cap;
- Python parse and compile succeed;
- structural markers identify a Skeleton Cast media source, including Flask app-style `/video` and health decorators;
- likely embedded plaintext credential assignment or sensitive dict literals block export. The scanner is identifier-aware for normalized names ending in or equal to common credential names such as API keys, access/auth/bot tokens, client/HMAC/secret keys, secrets, passwords/passwds, private keys, credentials, plus the media-search shorthand `TMDB_KEY` and `BRAVE_KEY`. Environment/getenv references, empty values, and obvious placeholders remain allowed.

The Runner independently decodes and revalidates the source from the private executor response, recomputes SHA-256 and byte count, reruns the credential assignment scanner, and atomically replaces the latest local snapshot only after the local hash and size match the remote public metadata.

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

No source text, source path, route implementation details, media titles/history, private addresses, credential names, secrets, HMAC values, local private artifact path, or live LAN values may appear in the public receipt.
