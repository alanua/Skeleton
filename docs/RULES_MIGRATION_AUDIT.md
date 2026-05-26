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

## Batch 1: current main evidence

| Evidence | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- |
| `+` means continue the current approved safe step and write only inside current scope. Source: COMMANDS.yaml. | WORKFLOW_RULE | P1 | COMMANDS.yaml | present |
| `фіксуй` / `зафіксуй` means durable persistence request; classify and route before writing. Source: COMMANDS.yaml. | CANON_BEHAVIOR | P1 | COMMANDS.yaml / MEMORY_ROUTING.yaml | present |
| `фіксуй` is enough approval to record the stated rule after classification; risky actions still require separate approval. Source: COMMANDS.yaml. | WORKFLOW_RULE | P1 | COMMANDS.yaml / MEMORY_ROUTING.yaml | present |
| Merge, deploy, runtime, secrets, and destructive operations require separate explicit approval. Source: COMMANDS.yaml and MEMORY_ROUTING.yaml. | SAFETY_GATE | P0 | MEMORY_ROUTING.yaml | present |
| Public-safe durable records route to GitHub canon review; private context routes privately; secrets never go to chat, GitHub, or plain Drive. Source: COMMANDS.yaml and MEMORY_ROUTING.yaml. | SAFETY_GATE | P0 | MEMORY_ROUTING.yaml | present |
| Routine safe helper steps may run as the smallest safe helper step without extra plus. Source: COMMANDS.yaml and MEMORY_ROUTING.yaml. | WORKFLOW_RULE | P1 | COMMANDS.yaml / MEMORY_ROUTING.yaml | present |
| Batch processing is only for same type, same approved route, same scope, same risk, same gate; split and stop on different items. Source: COMMANDS.yaml. | WORKFLOW_RULE | P1 | COMMANDS.yaml | present |
| Before Skeleton work or after recent main merge, check freshness: GitHub main, Runner checkout, sourcepack, open PRs/issues. Source: COMMANDS.yaml. | WORKFLOW_RULE | P1 | COMMANDS.yaml / docs/RUNNER_MAINTENANCE_TASKS.md | present but runtime allowlist mismatch observed |
| Old chats and old branches are not canon. Source: COMMANDS.yaml freshness rule. | CANON_BEHAVIOR | P0 | SOURCE_REGISTRY.yaml / COMMANDS.yaml | present |
| Status snapshots must not override current source truth; require last_verified for state files. Source: MEMORY_ROUTING.yaml. | CANON_BEHAVIOR | P1 | MEMORY_ROUTING.yaml | present |
| Repeated routine work should be processed as a pattern, batched only when route/scope/risk/gate match. Source: MEMORY_ROUTING.yaml. | WORKFLOW_RULE | P2 | MEMORY_ROUTING.yaml | present |

## Initial observed rules from recent work

- Skeleton is the control layer; Jeeves is a separate future assistant/product/runtime.
- Skeleton is P0, Aufmass is P1, BauClock and Lavalamp are later.
- Each project needs its own strategy file.
- After repeated blockers, stop similar tasks and repair the route first.
- Runtime maintenance tasks need exact allowed format and real allowlisted ids.

## Active blocker

Runner PR publishing from issue worktrees is blocking strategy work. Do not create more strategy content tasks until this route is repaired or a safe manual connector route is used.
