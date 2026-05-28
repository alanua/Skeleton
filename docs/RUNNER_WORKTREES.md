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

Target repository routing keeps the issue queue in `alanua/Skeleton`. Normal
task issues may set target metadata before the fenced task block. The runner
uses the first explicit repository field in this order: `Target Repository`,
`Selected Repository`, then `Repo`. The allowlisted targets are
`alanua/Skeleton`, `alanua/bauclock`, and `alanua/Lavalamp`; omitting the field
uses `alanua/Skeleton`.

For non-Skeleton target repositories, Codex runs inside that repository's
bounded issue worktree under `/home/agent/agent-dev/worktrees/<project>/issue-N`
and the git source checkout must be the allowlisted repository source under
`/home/agent/agent-dev/repos/<RepoName>`. For example, a Skeleton queue issue
that declares `Target Repository: alanua/Lavalamp` uses source
`/home/agent/agent-dev/repos/Lavalamp` and workdir
`/home/agent/agent-dev/worktrees/lavalamp/issue-N`. If the source checkout or
target worktree root is missing or unwritable, the runner blocks before Codex.
Task text cannot provide arbitrary checkout or worktree paths.

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
