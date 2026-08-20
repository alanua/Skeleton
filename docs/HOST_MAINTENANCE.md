# Host Maintenance Executor

`tools/skeleton_core/host_maintenance.py` is a bounded local executor for safe
Runner host upkeep. Version 0 accepts only structured YAML or JSON packets and
does not execute packet text as shell.

`tools/skeleton_core/host_maintenance_transport.py` is the bounded packet
transport for that executor. Its default transport root is:

```text
/home/agent/agent-dev/host_maintenance
```

The root contains three directories:

- `inbox/` receives YAML or JSON packets.
- `done/` receives packets whose executor report status is `ok`.
- `failed/` receives malformed packets and packets whose executor report status
  is `blocked`.

Each transport poll processes at most one packet, selected by sorted filename
from `inbox/`. An empty inbox writes a compact JSON no-op report and exits
without host action. The default transport report path is
`/home/agent/agent-dev/host_maintenance/host_maintenance_transport_report.json`.

Supported commands:

- `worktree_audit`
- `worktree_quarantine_clean_stale`
- `worktree_prune`
- `poller_status`
- `windows_bootstrap_audit`
- `windows_bootstrap_prepare_one_link`

The packet repository must be `alanua/Skeleton`. The default worktree root is
`/home/agent/agent-dev/worktrees/skeleton`, and candidate paths must resolve to
direct children named `issue-*` under that root. Paths outside that root and
`validate-pr-branch/*` paths are rejected before any action planning.

Example packet:

```yaml
command: worktree_quarantine_clean_stale
repository: alanua/Skeleton
apply: false
stale_days: 14
candidates:
  - /home/agent/agent-dev/worktrees/skeleton/issue-831
```

`apply` defaults to `false`. In dry-run mode, quarantine reports planned actions
only. With `apply: true`, eligible stale issue worktrees are moved into
`.quarantine/` under the worktree root. The executor never permanently deletes
worktrees.

Host-changing commands require `apply: true` in the packet, and that guard lives
in `host_maintenance.py`. The transport does not add another execution surface:
it only reads one packet, calls the bounded executor, writes JSON, and moves the
packet to `done/` or `failed/`.

Windows bootstrap starts with `windows_bootstrap_audit`. That command is always
read-only, ignores `apply: true`, creates no private artifact, and reports only
that owner approval is required for a one-link enrollment handoff.

`windows_bootstrap_prepare_one_link` is the protected runtime action for
preparing one owner Viber/private HTTPS enrollment link. It requires:

```yaml
command: windows_bootstrap_prepare_one_link
repository: alanua/Skeleton
apply: true
owner_approval: windows_bootstrap_one_link_v1
enrollment_id: win-target-01
```

The command writes the actual HTTPS link only to a `0600` private runtime
artifact under the configured private host-maintenance root. The enrollment
token is single-use, has no automatic expiry, and remains `ISSUED` until
successful enrollment or explicit revocation. The public JSON report contains
only the enrollment id, private artifact reference, SHA-256 hashes, status
tokens, `single_use: true`, `automatic_expiry: false`, and
`target_enrolled: false`. The public delivery-channel token may say
`owner_viber_private_https`, but the public report must not include the Viber
message content, HTTPS link, one-use code, target host identifiers, passwords,
keys, browser bypass instructions, SSH exposure, or any shell command supplied
by an issue. The owner must manually open the private HTTPS link on the
intended Windows target and verify the target fingerprint out of band before
enrollment is considered complete.

Candidates are skipped when they are missing, are not Git checkouts, have a
wrong `origin`, are dirty, or are not stale. Git inspection is limited to fixed
`git remote get-url origin` and `git status --porcelain` calls with no shell
interpolation.

Run locally:

```bash
python -m tools.skeleton_core.host_maintenance path/to/packet.yaml --report-path var/host_maintenance_report.json
```

The command writes deterministic compact JSON to the report path and prints the
same report. It returns exit code 0 for accepted packets and exit code 2 for
blocked packets.

Poll the bounded transport once:

```bash
python -m tools.skeleton_core.host_maintenance_transport
```

The transport returns exit code 0 for `done` and `no-op`, and exit code 2 for
`failed`. Override paths only with explicit flags:

```bash
python -m tools.skeleton_core.host_maintenance_transport \
  --transport-root /home/agent/agent-dev/host_maintenance \
  --worktree-root /home/agent/agent-dev/worktrees/skeleton
```

The one-link command additionally accepts an explicit private HTTPS base URL:

```bash
python -m tools.skeleton_core.host_maintenance_transport \
  --windows-bootstrap-base-url https://your-private-tailnet-name/windows
```

The direct executor accepts the same private Windows bootstrap options:

```bash
python -m tools.skeleton_core.host_maintenance path/to/packet.yaml \
  --private-runtime-root /home/agent/.local/share/skeleton/host-maintenance \
  --windows-bootstrap-base-url https://your-private-tailnet-name/windows
```

This executor must not run `sudo`, accept arbitrary shell commands, remove
files with permanent delete semantics, read secrets, touch private data, write
to the network, or clean `validate-pr-branch/*` workspaces.
