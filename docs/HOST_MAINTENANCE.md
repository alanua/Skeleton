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

This executor must not run `sudo`, accept arbitrary shell commands, remove
files with permanent delete semantics, read secrets, touch private data, write
to the network, or clean `validate-pr-branch/*` workspaces.
