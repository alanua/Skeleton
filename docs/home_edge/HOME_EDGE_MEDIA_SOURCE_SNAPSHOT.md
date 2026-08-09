# Home Edge Media Source Snapshot

`home_edge_01_media_source_snapshot_v1` is a fixed-purpose Runner runtime-maintenance operation for one read-only capture:

- repository: `alanua/Skeleton`
- target: `home-edge-01`
- public source identity token: `home_edge_01_skeleton_cast_app_py`
- execution lane: `read_only`
- run user: `desktop-user`
- timeout: `30` seconds
- transport: signed `core.home_edge.executor_gateway` request only
- operator approval: `EXPLICIT_MINIMAL_HOME_EDGE_SNAPSHOT_ACCESS_REPAIR_2026_08_09`

The operation is not a general file export facility. Issue metadata may provide only the runtime mode, exact maintenance task ID, repository, expected main SHA, and target. Path, command, script, output path, timeout, lane, user, node, and variant fields are rejected.

## Executor Authentication

The live Runner flow does not read the Home Edge executor HMAC and does not sign directly. It builds an unsigned request with the exact operator approval ref above, validates the fixed authority fields, and passes that JSON on stdin to the installed signer:

`sudo --non-interactive /usr/local/libexec/skeleton/home-edge/media-source-snapshot/signer`

The sudoers entry grants the Runner service user only that exact no-argument executable. The wrapper runs `/usr/bin/python3` with a minimal environment and an immutable root-owned payload at:

`/usr/local/lib/skeleton/home-edge/media-source-snapshot/signer_payload.py`

The payload imports only the Python standard library. It validates there are no argv arguments, bounds stdin, parses the unsigned request as JSON, rejects missing, empty, or different `operator_approval_ref`, and rechecks the exact node, lane, run user, script, interpreter, timeout, output cap, public flag, cwd absence, environment absence, stdin absence, argv absence, request id, nonce, and idempotency authority before opening any credential file.

Only after that approval and authority validation does the signer read `/etc/skeleton/home-edge-executor-controller.env`, and only the single allowlisted variable `SKELETON_HOME_EDGE_EXEC_HMAC_SECRET`.

That fixed private config is parsed as text, never sourced or executed. The resolver never reads or parses `/etc/skeleton/home-edge-01.env`; that file is metadata-only corroboration for the private controller ownership boundary. The fixed `/etc/skeleton` directory plus `/etc/skeleton/home-edge-01.env` and `/etc/skeleton/home-edge-executor-controller.env` are all checked with `lstat`; symlinks are rejected, `/etc/skeleton` must be a directory, both env paths must be regular files, none may be group/world writable, and each env file must be no larger than 64 KiB. The controller env is accepted when its owner is root or the current Runner uid, or when those three fixed paths share one identical owner and group boundary under the same strict checks. The parser accepts only simple `KEY=VALUE` or optional `export KEY=VALUE` entries for the allowlisted variable, ignoring comments, blank lines, and unrelated assignments. Duplicate target entries, NUL bytes, malformed quoted values, shell substitution or variable references, backticks, and multiline continuations fail closed before any executor request is made.

Authentication setup failures are reported only as stable public-safe classes: `executor_auth_config_missing`, `executor_auth_config_unsafe`, or `executor_auth_config_invalid`. The credential value, config path, and variable name are never included in public receipts.

After signing, the Runner reconstructs the executor request and revalidates the signed payload against the exact unsigned authority it originally built. Any changed approval, command, target, lane, user, script, interpreter, timeout, output cap, public flag, cwd, environment, stdin, argv, request id, nonce, or idempotency field blocks before `execute_home_edge_request` is called.

## Validation Boundary

Before any Home Edge request is constructed or signed, the Runner first checks the fixed private artifact location. If a regular, non-symlink, owner-context-safe, private-mode artifact already exists, the Runner reads it with the same 700 KiB bound, reruns UTF-8, credential, Python parse, route, and Skeleton Cast media validation locally, recomputes SHA-256 and byte count, and returns `success_criteria=met` with `stable_reason=already_captured`. That local one-shot path performs zero executor calls and reports only the aggregate `not_required_existing_capture` executor marker.

If the existing artifact is missing, the first capture uses a fresh attempt-scoped executor idempotency key and the installed signer described above. If transport fails ambiguously before the private artifact is published, the operation fails closed without retrying or writing an artifact; another capture requires a deliberate v2 or newly approved operation rather than unbounded retries.

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
