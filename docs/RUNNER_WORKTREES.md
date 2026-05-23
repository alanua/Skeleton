# Runner Worktrees

Runner worktree execution maps one normal bounded GitHub issue to one
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

Target repository routing stage 1 keeps the issue queue in `alanua/Skeleton`.
Normal task issues may set `Target Repository: <owner/repo>` before the fenced
task block. The allowlisted targets are `alanua/Skeleton`, `alanua/bauclock`,
and `alanua/Lavalamp`; omitting the field uses `alanua/Skeleton`.

Target repository routing stage 2 runs Codex in deterministic per-target
worktree roots only:

- `alanua/Skeleton`: `$SKELETON_WORKTREE_ROOT/issue-N`
- `alanua/bauclock`: `$SKELETON_WORKTREE_ROOT/../bauclock/issue-N`
- `alanua/Lavalamp`: `$SKELETON_WORKTREE_ROOT/../lavalamp/issue-N`

For non-Skeleton targets, Runner expects a main checkout at
`$SKELETON_WORKTREE_ROOT/../<target>/main`. If that checkout path is missing,
the issue is blocked with the exact missing path. Before Codex runs, Runner
verifies `origin` resolves to the selected target repository. File changes are
committed, pushed, and opened as a draft PR in the selected target repository.
Issue polling, queue labels, comments, and Telegram merge request issues remain
in `alanua/Skeleton`.

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
