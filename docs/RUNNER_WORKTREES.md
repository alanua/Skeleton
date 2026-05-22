# Runner Worktrees

Runner worktree execution stage 1 maps one bounded GitHub issue to one git
worktree, one `runner/issue-N` branch, and one draft PR.

The main Skeleton checkout is the coordinator. It polls GitHub issues and
changes Runner labels, but it does not run the issue Codex task or the task
validation commands. For each issue the Runner creates or reuses an issue
worktree under `SKELETON_WORKTREE_ROOT`; when that variable is unset the root
defaults to `/home/agent/agent-dev/worktrees/skeleton`.

Stage 1 is single-lane. The poller still processes ready issues one at a time;
it does not add a parallel timer, concurrent issue execution, or runner lanes.
Moving task execution out of the coordinator checkout reduces dirty-checkout
conflicts between issue work and queue coordination before parallel execution
exists.

An existing issue worktree is reused only when it is clean and already on the
expected `runner/issue-N` branch. Otherwise the issue is blocked with cleanup
guidance. Cleanup after success is best effort and is limited to paths under
the configured worktree root.

Future stages can add file locks, Runner lanes, and the Antigravity helper
route after this execution boundary is stable.

This stage does not deploy anything, change secrets, or change runtime service
configuration.
