# ProjectTree

`PROJECT_TREE.yaml` is the stage 1 ProjectTree metadata layer for future
multi-project routing and Runner git worktree planning.

It declares:

- the metadata `version`;
- the `default_project`;
- project entries with repository route metadata, public/private boundaries,
  future parallel worktree eligibility, runtime approval requirements, and a
  worktree name prefix.

`core/project_tree.py` contains pure helpers for loading and validating the
metadata, reading one project entry, and planning deterministic future worktree
names. Project ids are bounded identifiers so path traversal values do not enter
future worktree routing.

ProjectTree does not schedule Runner work, start parallel jobs, run git, call
subprocesses, or write to the filesystem. Stage 1 is control metadata and tests
only.

Stage 1 enables local Codex issue worktree execution for `skeleton` and
`bauclock`. BauClock remains a local-worktree-only route:
`planning_only=false`, `codex_issue_worktree=true`, and
`live_cross_repo=false`. Because live cross-repo execution is disabled, Runner
must not push BauClock branches, create BauClock pull requests, deploy, or use
secrets for BauClock tasks.
