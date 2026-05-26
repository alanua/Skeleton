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

## Batch 2: boot, source, project, and adapter evidence

| Evidence | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- |
| `BOOT_MANIFEST.yaml` is the confirmed boot route and declares the startup read order. | CANON_BEHAVIOR | P0 | BOOT_MANIFEST.yaml | present |
| Boot must produce a BootReport with repo, ref, entrypoint, loaded_sources, mode, active_project_status, source_trust_map, and writes. | WORKFLOW_RULE | P1 | BOOT_MANIFEST.yaml / boot_loader docs | present |
| Source trust order starts with current user message for runtime instructions, then boot manifest, then public GitHub canon, private memory, weak ChatGPT memory, archive evidence. | CANON_BEHAVIOR | P0 | SOURCE_REGISTRY.yaml | present |
| ChatGPT memory is weak cache and requires verification for serious claims. | CANON_BEHAVIOR | P0 | SOURCE_REGISTRY.yaml / MEMORY_ROUTING.yaml | present |
| Archive history is evidence on demand, not active route. | CANON_BEHAVIOR | P1 | SOURCE_REGISTRY.yaml | present |
| ChatGPT role is planner/operator interface, reviewer, framer, memory organizer. | WORKFLOW_RULE | P1 | SOURCE_REGISTRY.yaml / adapters/chatgpt | present |
| Skeleton is model-neutral control layer for LLM-assisted work. | CANON_BEHAVIOR | P0 | SOURCE_REGISTRY.yaml / BOOT_MANIFEST.yaml | present |
| Runner is execution bridge for bounded tasks; Codex is bounded coding executor; Gemini is auditor/second-brain role. | WORKFLOW_RULE | P1 | SOURCE_REGISTRY.yaml / adapters | present |
| Jeeves is separate future assistant product and runtime. | CANON_BEHAVIOR | P0 | SOURCE_REGISTRY.yaml / projects/jeeves | present |
| Projects are routed through PROJECT_INDEX.yaml to per-project PROJECT_MANIFEST.yaml entrypoints. | WORKFLOW_RULE | P1 | PROJECT_INDEX.yaml / project_loader | present |
| ChatGPT adapter must load Skeleton through BOOT_MANIFEST.yaml and must not merge, deploy, access secrets, or write durable canon without explicit operator approval. | SAFETY_GATE | P0 | adapters/chatgpt/START_HERE.md | present |
| ChatGPT may frame work and draft reviewable changes, but authority comes from operator plus declared Skeleton manifests and contracts. | CANON_BEHAVIOR | P0 | adapters/chatgpt/START_HERE.md | present |

## Initial observed rules from recent work

- Skeleton is P0, Aufmass is P1, BauClock and Lavalamp are later.
- Each project needs its own strategy file.
- After repeated blockers, stop similar tasks and repair the route first.
- Runtime maintenance tasks need exact allowed format and real allowlisted ids.

## Active blocker

Runner PR publishing from issue worktrees is blocking strategy work. Do not create more strategy content tasks until this route is repaired or a safe manual connector route is used.
