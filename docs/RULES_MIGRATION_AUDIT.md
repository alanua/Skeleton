# Rules Migration Audit

Purpose: collect behavior rules from old Skeleton, current Skeleton, branches, issues, PRs, and adapter files before promoting them to canon.

## Method

1. Collect evidence.
2. Classify each rule.
3. Rank by importance.
4. Remove duplicates and conflicts.
5. Write accepted rules to the correct target file.

## Classes

- CANON_BEHAVIOR
- SAFETY_GATE
- WORKFLOW_RULE
- ADAPTIVE_LESSON
- PROJECT_STRATEGY
- TOOL_FORMAT_RULE
- BACKLOG_OR_IDEA
- OUTDATED_OR_REJECTED
- PRIVATE

## Ranking

- P0 safety and control gates
- P1 Skeleton core and development workflow
- P2 adaptive learning and anti-repeat-failure rules
- P3 project strategy routing
- P4 reporting style and convenience

## Initial observed rules

- `fixuy` means classify and record through the correct route.
- `+` means continue the already proposed safe next step.
- Merge, deploy, runtime, secrets, and destructive operations require separate explicit approval.
- Skeleton is the control layer; Jeeves is a separate future assistant/product/runtime.
- Skeleton is P0, Aufmass is P1, BauClock and Lavalamp are later.
- Each project needs its own strategy file.
- After repeated blockers, stop similar tasks and repair the route first.
- Runtime maintenance tasks need exact allowed format and real allowlisted ids.

## Active blocker

Runner PR publishing from issue worktrees is blocking strategy work. Do not create more strategy content tasks until this route is repaired or a safe manual connector route is used.
