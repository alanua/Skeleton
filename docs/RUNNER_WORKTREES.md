# Runner Worktrees

Runner worktree execution stage 1 maps one normal bounded GitHub issue to one
git worktree, one `runner/issue-N` branch, and one draft PR.

The main Skeleton checkout is the coordinator. It polls GitHub issues and
changes Runner labels, but normal issue Codex execution, task validation,
commits, pushes, and draft PR creation run in the per-issue worktree. The
worktree root comes from `SKELETON_WORKTREE_ROOT` when set and otherwise
defaults to `/home/agent/agent-dev/worktrees/skeleton`.

Stage 1 is single-runner execution. The poller still processes ready issues one
at a time; it does not add a parallel timer or concurrent issue execution.
Moving normal task execution out of the coordinator checkout reduces dirty
checkout conflicts between issue work and queue coordination before parallel
execution exists.

Runner lane stage 3 makes reserved lane names visible in GitHub before future
routing. Normal task issues may set `Runner Lane: <name>` before the fenced
task block; omitting it uses `default`. The allowlisted names are `default`,
`lane-1`, and `lane-2`. When lane metadata is present, the poller applies the
matching `runner:lane:*` GitHub label and repeats the parsed lane in the final
Runner report. Lane labels are status markers only: this stage does not route,
prioritize, lock, or parallelize work by lane.
Lane metadata smoke tests should confirm `runner:lane:lane-1` and
`Runner Lane: lane-1` appear while execution remains single-runner.

Target worktree stage 2 lets normal task issues set
`Target Repository: <owner/name>` before the fenced task block. Omitting it uses
`alanua/Skeleton`. The allowlisted target repositories are `alanua/Skeleton` and
`alanua/jeeves`; any other value is blocked before labels are claimed or Codex is
run.

The target repository metadata controls worktree path planning, coordinator
checkout selection, base branch, branch name, push target, and draft PR
creation. `alanua/Skeleton` keeps the legacy `SKELETON_WORKTREE_ROOT` override.
Other allowlisted repositories may set
`SKELETON_WORKTREE_ROOT_<OWNER>_<NAME>` and
`SKELETON_COORDINATOR_WORKDIR_<OWNER>_<NAME>`, for example
`SKELETON_WORKTREE_ROOT_ALANUA_JEEVES` and
`SKELETON_COORDINATOR_WORKDIR_ALANUA_JEEVES`.

Runner-created PRs remain draft PRs. Merge automation stays gated on an explicit
Telegram-approved merge request and still refuses draft PRs.

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
