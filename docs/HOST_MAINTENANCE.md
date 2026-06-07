# Host Maintenance Executor

`tools/skeleton_core/host_maintenance.py` is a bounded local executor for safe
Runner host upkeep. Version 0 accepts only structured YAML or JSON packets and
does not execute packet text as shell.

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

This executor must not run `sudo`, accept arbitrary shell commands, remove
files with permanent delete semantics, read secrets, touch private data, write
to the network, or clean `validate-pr-branch/*` workspaces.
