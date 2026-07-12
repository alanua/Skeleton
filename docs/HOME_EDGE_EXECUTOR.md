# Home Edge Executor

`home_edge_exec` is the universal Home Edge execution contract for `home-edge-01`.
It replaces per-command and per-device runtime handlers with one bounded request and
receipt model.

The supported controller route is the existing strict OpenSSH over the private
Tailscale profile. The controller submits JSON through the exact node-side
command `/usr/local/bin/home_edge_exec --server`. That public wrapper invokes
only `sudo -n -- /usr/local/sbin/home_edge_exec_root --server`; the root wrapper
loads the root-private environment, clears unsafe inherited environment, reads
one request from stdin, executes either shell-free `argv` or explicit bounded
script mode, returns one receipt on stdout, and exits. There is no
always-running executor service.

Execution lanes:

- `read_only`: inspection, status, logs, inventories and diagnostics. It runs
  as `desktop-user` unless a separate `operator_approval_ref` beginning with
  `root-read-only:` explicitly approves root read-only mode.
- `routine_mutation`: ordinary operator-requested service, media, process or
  config changes. It requires `operator_approval_ref` and must run as
  `desktop-user`.
- `privileged_mutation`: root/system changes, package operations, device
  rebinding, firewall or network changes. It requires `operator_approval_ref`
  and must run as `root`.
- `destructive`: erase, delete, restore, disk or firmware destructive work. It
  requires `operator_approval_ref` and must run as `root`.

Request security fails closed. The node requires `SKELETON_HOME_EDGE_EXEC_HMAC_SECRET`;
every executable request must include `timestamp`, `nonce` and a valid
`sha256=` HMAC signature computed after all authenticated request fields are
finalized. The presentation-only `public` field is excluded from the canonical
signature payload so controller, MCP and node verification use the same command
representation while still allowing public receipt redaction. Unsigned, stale
and bad-signature requests are rejected before process launch.

Nonce and idempotency state is persisted in the configured node state file
(`SKELETON_HOME_EDGE_EXEC_IDEMPOTENCY_CACHE` for compatibility). The state file
is protected with an exclusive file lock and atomic replacement. A nonce can
execute only once. An idempotent retry can return a cached receipt only when the
canonical payload digest matches the original request; reusing an idempotency
key for different payload blocks.

Execution identity is derived from the effective UID. A root node process does
not directly run `desktop-user` requests as root; it switches with sudo to the
configured real account from `SKELETON_HOME_EDGE_DESKTOP_USER` and fails closed
if that account cannot be resolved. `desktop-user` execution receives a
deterministic desktop session environment: `HOME`, `USER`, `LOGNAME`,
`XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS`, configured display values, a
small inherited allowlist and explicitly permitted request overrides.

Private receipts retain bounded stdout/stderr for audit and controller use.
When `public=true`, receipts expose only status, a bounded public-safe summary,
timestamps, lane, exit code, idempotency state and receipt hash. Public receipts
never include raw argv, script text, stdin, cwd, environment values, SSH target
details, private paths or raw remote errors.

## Node installation

Install the node-side one-shot executor on `home-edge-01` by providing the
private HMAC value on stdin:

```bash
sudo scripts/install_home_edge_executor.sh \
  --desktop-user "$REAL_DESKTOP_USER" \
  --ssh-target-user "$STRICT_SSH_TARGET_USER" \
  --replace-secret-stdin
```

The secret is never accepted as an argv value. It may come only from stdin with
`--replace-secret-stdin` or from an already-private
`SKELETON_HOME_EDGE_EXEC_HMAC_SECRET` environment source in a root-private
process. Re-running the installer preserves the existing private env file secret
unless `--replace-secret-stdin` is explicitly supplied.

The installer creates:

- `/usr/local/bin/home_edge_exec`, the stable strict-OpenSSH command target.
- `/usr/local/sbin/home_edge_exec_root`, the root-only one-shot wrapper invoked
  only by sudo.
- `/usr/local/lib/skeleton-home-edge-executor`, the Python files needed by
  `home_edge_exec --server`.
- `/etc/skeleton/home_edge_executor.env`, mode `0600`, containing only private
  node runtime configuration.
- `/etc/sudoers.d/skeleton-home-edge-executor`, mode `0440`, granting only the
  configured strict SSH target user `NOPASSWD` access to
  `/usr/local/sbin/home_edge_exec_root --server`.
- `/var/lib/skeleton/home_edge_exec` and `/var/log/skeleton/home_edge_exec`,
  mode `0700`, for nonce/idempotency state, cancel files and audit output.

The public wrapper supports only `home_edge_exec --server` and cannot read the
private env file. It invokes only `sudo -n --
/usr/local/sbin/home_edge_exec_root --server`. The root wrapper accepts exactly
`--server`, loads `/etc/skeleton/home_edge_executor.env`, clears unsafe inherited
environment, sets the installed Python path, reads exactly one signed JSON
request from stdin, writes one JSON receipt, and exits. It does not install or
enable a systemd unit.

Rollback is explicit: `scripts/install_home_edge_executor.sh --uninstall`
removes the wrapper, sudoers rule and installed Python files while preserving
the private env file as a timestamped backup. Normal installs also create
timestamped backups of existing target files before replacement.

CLI examples:

```bash
python3 scripts/home_edge_exec.py --lane read_only -- uname -a
python3 scripts/home_edge_exec.py --lane read_only --script 'printf "%s\n" "$USER"'
python3 scripts/home_edge_exec.py --request-json /private/home_edge/request.json
python3 scripts/home_edge_exec_mcp.py
```

All controller CLI examples require `SKELETON_HOME_EDGE_EXEC_HMAC_SECRET` in the
controller environment. The CLI signs only after timestamps, nonce and request
fields are finalized, and exits blocked when the secret is missing.

MCP callers do not provide or receive HMAC secrets or signatures. The MCP server
loads `SKELETON_HOME_EDGE_EXEC_HMAC_SECRET` from its private environment,
generates timestamp and nonce internally after caller arguments are final, signs
the request, and fails closed with a public-safe error if the private secret is
not configured.

This repository task does not deploy the service or perform live Home Edge actions.
