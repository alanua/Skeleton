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

ProjectTree does not schedule Runner work, start parallel jobs, create
worktrees, run git, call subprocesses, or write to the filesystem. Runner uses
the metadata to decide which projects may execute from issue worktrees:
Skeleton runs through the normal Skeleton issue-worktree PR flow, BauClock runs
through the local target-project issue-worktree Stage 1 route without creating
target-repo output, and Lavalamp remains planning-only.
