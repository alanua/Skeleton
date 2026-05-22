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

Runner lane stage 1 reserves lane names for future routing. Normal task issues
may set `Runner Lane: <name>` before the fenced task block; omitting it uses
`default`. The allowlisted names are `default`, `lane-1`, and `lane-2`. The
poller validates and stores the parsed lane with the task model, but stage 1
does not route, prioritize, lock, or parallelize work by lane.
Lane metadata smoke tests should confirm `Runner Lane: lane-1` appears in the
Runner `DONE` report while execution remains single-runner.

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
