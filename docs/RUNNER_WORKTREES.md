# Runner Issue Workspaces

Runner worktree execution stage 1 maps one normal bounded GitHub issue to one
standalone issue workspace, one `runner/issue-N` branch, and one draft PR.

The main Skeleton checkout is the coordinator. It polls GitHub issues and
changes Runner labels, but normal issue Codex execution, task validation,
commits, pushes, and draft PR creation run in the per-issue workspace. The
workspace root comes from `SKELETON_WORKTREE_ROOT` when set and otherwise
defaults to `/home/agent/agent-dev/worktrees/skeleton`.

Issue workspaces are standalone local clones. They are not linked Git
worktrees, because linked worktrees store writable per-worktree metadata under
the coordinator checkout's `.git/worktrees` directory instead of inside the
assigned issue workspace.

Stage 1 is single-runner execution. The poller still processes ready issues one
at a time; it does not add a parallel timer or concurrent issue execution.
Moving normal task execution out of the coordinator checkout reduces dirty
checkout conflicts and keeps Git metadata writes inside the assigned issue
workspace before parallel execution exists.

Runner lane stage 3 makes reserved lane names visible in GitHub before future
routing. Normal task issues may set `Runner Lane: <name>` before the fenced
task block; omitting it uses `default`. The allowlisted names are `default`,
`lane-1`, and `lane-2`. When lane metadata is present, the poller applies the
matching `runner:lane:*` GitHub label and repeats the parsed lane in the final
Runner report. Lane labels are status markers only: this stage does not route,
prioritize, lock, or parallelize work by lane.
Lane metadata smoke tests should confirm `runner:lane:lane-1` and
`Runner Lane: lane-1` appear while execution remains single-runner.

Target repository routing keeps the issue queue in `alanua/Skeleton`. Normal
task issues may set `Target Repository: <owner/repo>` before the fenced task
block. The compatibility aliases `Selected Repository:` and `Repo:` are also
accepted, in that priority order after `Target Repository:`. The allowlisted
targets are the public projects registered in `PROJECT_TREE.yaml`; omitting the
field selects `alanua/Skeleton`.

Execution policy is also read from `PROJECT_TREE.yaml`. Skeleton tasks continue
to use Skeleton issue worktrees. Non-Skeleton targets that enable
`codex_issue_worktree` use their registered `worktree_root`, and Runner blocks
before Codex when the registered checkout path is outside the approved Runner
bases, missing, or not a Git checkout. `live_cross_repo` remains disabled for
all projects, so pushes and draft PR creation outside this task remain a
separate gate.

An existing issue worktree is reused only when it is clean and already on the
expected `runner/issue-N` branch. Otherwise the issue is blocked with cleanup
guidance. Cleanup after success is best effort and is limited to paths under
the configured worktree root.

Runtime maintenance tasks are separate allowlisted host-runner actions. They
continue to bypass Codex and do not use Codex issue worktrees.

Future stages can add file locks, lane routing, and the Antigravity helper route
after this execution boundary is stable.

This stage does not deploy anything, change secrets, or change runtime service
configuration.
