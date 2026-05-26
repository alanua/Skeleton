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

## Batch 1: current command and memory evidence

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

## Batch 3: Runner maintenance and tool-format evidence

| Evidence | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- |
| Runtime maintenance tasks are host Runner actions, not Codex tasks. | SAFETY_GATE | P0 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| Codex stays inside its workspace sandbox and must not be asked to reach systemd or host runtime paths. | SAFETY_GATE | P0 | docs/RUNNER_MAINTENANCE_TASKS.md / adapter contracts | present |
| Runtime maintenance issue format requires `Mode: RUNTIME_MAINTENANCE_TASK` and `Maintenance Task ID:`. | TOOL_FORMAT_RULE | P1 | docs/RUNNER_MAINTENANCE_TASKS.md / COMMANDS.yaml | present |
| Issue text is not a shell script; host Runner dispatches only task ids in its code allowlist. | SAFETY_GATE | P0 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| Missing or unknown maintenance task ids are reported as `BLOCKED`. | TOOL_FORMAT_RULE | P1 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| Privileged host commands must use non-interactive `sudo -n`; Runner blocks instead of waiting for input. | SAFETY_GATE | P0 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| `check_project_checkout` and `ensure_project_checkout` must include `Target Project:` and resolve paths through PROJECT_TREE. | TOOL_FORMAT_RULE | P1 | docs/RUNNER_MAINTENANCE_TASKS.md / PROJECT_TREE docs | present |
| `validate_pr_branch` requires `Pull Request:` and only allows validation profiles `full_pytest` and `knowledge_intake`. | TOOL_FORMAT_RULE | P1 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| `check_skeleton_freshness` is documented as an allowlisted maintenance id, but runtime reported it not allowlisted in #518. | ADAPTIVE_LESSON | P2 | docs/ADAPTIVE_PRACTICAL_LEARNING.md / Runner repair task | conflict observed |
| Maintenance reports must state `DONE` or `BLOCKED` accurately; failed runtime verification is `BLOCKED`. | WORKFLOW_RULE | P1 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| Reports must not print token values or raw command output. | SAFETY_GATE | P0 | docs/RUNNER_MAINTENANCE_TASKS.md | present |

## Batch 4: all-project evidence

| Project | Evidence | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- | --- |
| skeleton | Skeleton is the active core project and control layer; public-safe repo is alanua/Skeleton. | PROJECT_STRATEGY | P1 | projects/skeleton/STRATEGY.md | present in manifest, needs strategy file |
| aufmass | Public-safe methods may live in Skeleton; real drawings and real quantities stay private. | PROJECT_STRATEGY | P1 | projects/aufmass/STRATEGY.md | present in manifest/state, needs strategy file |
| jeeves | Jeeves is separate from Skeleton; alanua/jeeves remains canonical for runtime/product code; Skeleton may govern tasks but not become Jeeves runtime. | CANON_BEHAVIOR | P0 | projects/jeeves/STRATEGY.md / docs/JEEVES_BRIDGE.md | present in state, needs strategy file |
| homelab | Homelab includes HA, Proxmox, ESPHome, ESP32, MQTT, Node-RED; device inventory, LAN details, and credentials require private route handling. | PROJECT_STRATEGY | P2 | projects/homelab/STRATEGY.md | present in state, needs strategy file |
| gewerbe | Gewerbe is private-sensitive; personal/tax/finance/official-document details must not be public by default; legal/tax/finance claims need high-accuracy audit mode. | SAFETY_GATE | P0 | projects/gewerbe/STRATEGY.md | present in state, needs strategy file |
| van | Van conversion may keep public-safe design rules in Skeleton but photos, logistics, and private planning details require private routes. | PROJECT_STRATEGY | P3 | projects/van/STRATEGY.md | present in state, needs strategy file |
| bauclock | BauClock has legal, privacy, audit, role-isolation, dashboard-token, and export boundary requirements. | PROJECT_STRATEGY | P3 | projects/bauclock/STRATEGY.md | present in manifest/state, needs strategy file |
| lavalamp | Lavalamp is separate from general home automation and separate from Jeeves runtime decisions. | PROJECT_STRATEGY | P4 | projects/lavalamp/STRATEGY.md | present in manifest/state, needs strategy file |
| all projects | Current manifests mostly read PROJECT_MANIFEST and STATE only; they do not yet point to STRATEGY.md. | ADAPTIVE_LESSON | P2 | project manifests / project loader docs | gap observed |
| all projects | Each project needs its own STRATEGY.md; cross-project ordering belongs in docs/WORKING_STRATEGY.md. | PROJECT_STRATEGY | P1 | per-project STRATEGY.md / docs/WORKING_STRATEGY.md | proposed |
| all projects | Before creating a task, Skeleton should load the selected project strategy; if projects compete, load WORKING_STRATEGY. | WORKFLOW_RULE | P1 | COMMANDS.yaml / project manifests / project_loader docs | proposed |

## Batch 5: branch scan evidence

| Branch group | Evidence | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- | --- |
| chatgpt/adaptive-practical-learning | Branch is one commit ahead and adds docs/ADAPTIVE_PRACTICAL_LEARNING.md. It records practical adaptation after repeated blockers. | ADAPTIVE_LESSON | P2 | docs/ADAPTIVE_PRACTICAL_LEARNING.md / MEMORY_ROUTING.yaml | candidate branch |
| chatgpt/rules-migration-audit | Branch is ahead and contains this audit file. Purpose is evidence collection, classification, ranking, and later targeted canon updates. | WORKFLOW_RULE | P1 | docs/RULES_MIGRATION_AUDIT.md | active audit branch |
| chatgpt/pr3-state-manifest-doc | Branch is diverged and far behind main; only README.md differs. Treat as historical review, not active canon route. | OUTDATED_OR_REJECTED | P4 | archive review only | stale branch |
| feat/system-prompts-chatgpt-gemini | Branch is behind main with no commits ahead. Treat as already merged, superseded, or historical unless specific evidence is requested. | OUTDATED_OR_REJECTED | P4 | archive review only | stale branch |
| runner/issue-* | Many runner branches remain from completed or blocked issue work. Branch existence alone is not canon; merged PRs and main files are stronger evidence. | WORKFLOW_RULE | P1 | cleanup policy / branch hygiene | needs cleanup policy |
| runner/issue-* | Search found runner branches through issue-509. They should be reviewed by issue/PR status, not blindly imported as rules. | ADAPTIVE_LESSON | P2 | docs/WORKING_STRATEGY.md / cleanup tasks | evidence rule |

## Active blocker

Runner PR publishing from issue worktrees is blocking strategy work. Do not create more strategy content tasks until this route is repaired or a safe manual connector route is used.

## Next audit steps

1. Review merged PRs and closed issues for additional behavior rules.
2. Compare stale branches only when they contain files not represented on main.
3. Promote accepted rules into target files in small PRs.
4. Keep private project details out of public GitHub.
