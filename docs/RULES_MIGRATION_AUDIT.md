# Rules Migration Audit

Purpose: collect behavior rules from Skeleton files, branches, issues, PRs, and live operation before promoting accepted rules to canon target files.

Status: review evidence only, not final canon promotion.

## Method

1. Collect evidence.
2. Classify each rule.
3. Rank by importance.
4. Remove duplicates and conflicts.
5. Promote accepted rules into the correct target file through small reviewable changes.

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

- P0: safety, control, and source-trust rules
- P1: Skeleton core and development workflow
- P2: adaptive learning and anti-repeat-failure rules
- P3: project strategy routing
- P4: reporting and convenience rules

## Batch 1: command and memory evidence

| Evidence | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- |
| `+` continues the current approved safe step inside current scope. | WORKFLOW_RULE | P1 | COMMANDS.yaml | present |
| `фіксуй` / `зафіксуй` means durable persistence request after classification and routing. | CANON_BEHAVIOR | P1 | COMMANDS.yaml / MEMORY_ROUTING.yaml | present |
| Risky gates need separate explicit approval. | SAFETY_GATE | P0 | COMMANDS.yaml / MEMORY_ROUTING.yaml | present |
| Public-safe durable records route to GitHub review; private context routes privately. | SAFETY_GATE | P0 | MEMORY_ROUTING.yaml | present |
| Routine safe helper steps may run as the smallest safe helper step without extra plus. | WORKFLOW_RULE | P1 | COMMANDS.yaml / MEMORY_ROUTING.yaml | present |
| Batch work is allowed only when route, scope, risk, and gate match. | WORKFLOW_RULE | P1 | COMMANDS.yaml | present |
| Old chats and old branches are evidence, not canon. | CANON_BEHAVIOR | P0 | SOURCE_REGISTRY.yaml / COMMANDS.yaml | present |
| State snapshots must not override current source truth. | CANON_BEHAVIOR | P1 | MEMORY_ROUTING.yaml | present |

## Batch 2: boot, source, project, and adapter evidence

| Evidence | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- |
| BOOT_MANIFEST.yaml is the confirmed boot route and startup read order. | CANON_BEHAVIOR | P0 | BOOT_MANIFEST.yaml | present |
| Boot should produce a BootReport with loaded sources, mode, project status, trust map, and writes. | WORKFLOW_RULE | P1 | BOOT_MANIFEST.yaml / boot_loader docs | present |
| Source trust order starts with current user message, then boot route, reviewed GitHub canon, private memory, weak ChatGPT memory, archive evidence. | CANON_BEHAVIOR | P0 | SOURCE_REGISTRY.yaml | present |
| ChatGPT memory is weak cache and requires verification for serious claims. | CANON_BEHAVIOR | P0 | SOURCE_REGISTRY.yaml / MEMORY_ROUTING.yaml | present |
| Archive history is evidence on demand, not active route. | CANON_BEHAVIOR | P1 | SOURCE_REGISTRY.yaml | present |
| Skeleton is the model-neutral control layer; Jeeves is a separate future product/runtime. | CANON_BEHAVIOR | P0 | SOURCE_REGISTRY.yaml / project strategies | present |
| ChatGPT is planner/operator interface and reviewer, not merge/deploy authority. | WORKFLOW_RULE | P1 | adapters/chatgpt/START_HERE.md | present |
| Project routing goes through PROJECT_INDEX.yaml and per-project PROJECT_MANIFEST.yaml. | WORKFLOW_RULE | P1 | PROJECT_INDEX.yaml / project_loader | present |

## Batch 3: Runner maintenance and tool-format evidence

| Evidence | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- |
| Runtime maintenance tasks are host Runner actions, not Codex tasks. | SAFETY_GATE | P0 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| Runtime maintenance format requires `Mode: RUNTIME_MAINTENANCE_TASK` and `Maintenance Task ID:`. | TOOL_FORMAT_RULE | P1 | docs/RUNNER_MAINTENANCE_TASKS.md / COMMANDS.yaml | present |
| Issue text is not a shell script; Runner dispatches only allowlisted task ids. | SAFETY_GATE | P0 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| Missing or unknown maintenance task ids are BLOCKED. | TOOL_FORMAT_RULE | P1 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| Checkout maintenance tasks need `Target Project:`. | TOOL_FORMAT_RULE | P1 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| PR validation tasks need `Pull Request:` and an allowed validation profile. | TOOL_FORMAT_RULE | P1 | docs/RUNNER_MAINTENANCE_TASKS.md | present |
| `check_skeleton_freshness` is documented but runtime reported it not allowlisted in #518. | ADAPTIVE_LESSON | P2 | Runner repair / adaptive notes | conflict observed |
| Maintenance reports must state DONE or BLOCKED accurately and avoid raw sensitive output. | WORKFLOW_RULE | P1 | docs/RUNNER_MAINTENANCE_TASKS.md | present |

## Batch 4: all-project evidence

| Project | Evidence | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- | --- |
| skeleton | Active core control layer; public-safe repo is alanua/Skeleton. | PROJECT_STRATEGY | P1 | projects/skeleton/STRATEGY.md | needs strategy file |
| aufmass | Public-safe methods may live in Skeleton; real drawings and real quantities stay private. | PROJECT_STRATEGY | P1 | projects/aufmass/STRATEGY.md | needs strategy file |
| jeeves | Separate from Skeleton; alanua/jeeves remains runtime/product source. | CANON_BEHAVIOR | P0 | projects/jeeves/STRATEGY.md / docs/JEEVES_BRIDGE.md | needs strategy file |
| homelab | Device inventory, LAN details, and credentials require private route handling. | PROJECT_STRATEGY | P2 | projects/homelab/STRATEGY.md | needs strategy file |
| gewerbe | Private-sensitive admin project; official/finance/tax claims need high-accuracy audit. | SAFETY_GATE | P0 | projects/gewerbe/STRATEGY.md | needs strategy file |
| van | Public-safe design rules may live in Skeleton; photos/logistics/private details need private route. | PROJECT_STRATEGY | P3 | projects/van/STRATEGY.md | needs strategy file |
| bauclock | Legal, privacy, audit, role-isolation, dashboard-token, and export boundaries apply. | PROJECT_STRATEGY | P3 | projects/bauclock/STRATEGY.md | needs strategy file |
| lavalamp | Separate WLED cylinder project, separate from home automation and Jeeves runtime. | PROJECT_STRATEGY | P4 | projects/lavalamp/STRATEGY.md | needs strategy file |
| all projects | Each project needs its own STRATEGY.md; cross-project order belongs in WORKING_STRATEGY. | PROJECT_STRATEGY | P1 | per-project STRATEGY.md / docs/WORKING_STRATEGY.md | proposed |
| all projects | Before task creation, load project strategy; if projects compete, load WORKING_STRATEGY. | WORKFLOW_RULE | P1 | COMMANDS.yaml / project manifests | proposed |

## Batch 5: branch scan evidence

| Branch group | Evidence | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- | --- |
| chatgpt/adaptive-practical-learning | Adds adaptive-practical-learning candidate doc. | ADAPTIVE_LESSON | P2 | docs/ADAPTIVE_PRACTICAL_LEARNING.md / MEMORY_ROUTING.yaml | candidate branch |
| chatgpt/rules-migration-audit | Active audit branch for evidence, classification, ranking, and later target updates. | WORKFLOW_RULE | P1 | docs/RULES_MIGRATION_AUDIT.md | active audit branch |
| stale chatgpt/feat branches | Treat as historical unless specific evidence is requested. | OUTDATED_OR_REJECTED | P4 | archive review only | stale branch |
| runner/issue-* | Branch existence alone is not canon; review by issue/PR status. | ADAPTIVE_LESSON | P2 | cleanup policy / working strategy | evidence rule |

## Batch 6: live behavior-rule intake

| Rule | Class | Rank | Target | Status |
| --- | --- | --- | --- | --- |
| Skeleton is a control layer, not ordinary chat; assistant must work through project, strategy, route, blocker, and smallest practical step. | CANON_BEHAVIOR | P0 | COMMANDS.yaml / MEMORY_ROUTING.yaml / adapters/chatgpt/START_HERE.md | candidate |
| `fixuy`, `фіксуй`, and `зафіксуй` are memory-routing events requiring classification, route selection, and immediate behavior change. | CANON_BEHAVIOR | P0 | COMMANDS.yaml / MEMORY_ROUTING.yaml | candidate |
| Fixation without changed next action does not count. | ADAPTIVE_LESSON | P1 | MEMORY_ROUTING.yaml / docs/ADAPTIVE_PRACTICAL_LEARNING.md | candidate |
| Use existing Skeleton capabilities instead of pretending they do not exist. | WORKFLOW_RULE | P1 | CAPABILITY_REGISTRY.yaml / MEMORY_ROUTING.yaml | candidate |
| memory_manager and memory_store are tested stage 1 memory tools and should guide classification/routing even before live storage. | WORKFLOW_RULE | P1 | docs/MEMORY_MANAGER.md / docs/MEMORY_STORE.md / MEMORY_ROUTING.yaml | candidate |
| Repeated blocker becomes ACTIVE_BLOCKER: stop similar tasks and repair the real route. | ADAPTIVE_LESSON | P1 | MEMORY_ROUTING.yaml / docs/ADAPTIVE_PRACTICAL_LEARNING.md | candidate |
| Adaptation must create practical progress, not extra paperwork. | CANON_BEHAVIOR | P1 | MEMORY_ROUTING.yaml / adapters/chatgpt/START_HERE.md | candidate |
| Technical blocker repair must serve delivery of behavior rules and strategy canonization, not replace them. | WORKFLOW_RULE | P1 | docs/WORKING_STRATEGY.md / docs/ADAPTIVE_PRACTICAL_LEARNING.md | candidate |
| Small public-safe steps may use GitHub connector when Runner is blocked; risky gates still need explicit approval. | WORKFLOW_RULE | P1 | COMMANDS.yaml / MEMORY_ROUTING.yaml | candidate |
| Reports should be short: classification, route, done, next practical step. | WORKFLOW_RULE | P2 | adapters/chatgpt/START_HERE.md / COMMANDS.yaml | candidate |
| Issue or PR number means read it, check scope/tests/data/runtime risk, then approve, reject, or state blocker and one next step. | WORKFLOW_RULE | P1 | COMMANDS.yaml / DEV_DEPARTMENT_WORKFLOW.md | candidate |
| `+` means continue the already proposed safe next step; do not re-plan unless new evidence changes route. | WORKFLOW_RULE | P1 | COMMANDS.yaml | candidate |
| Chat, old branches, and old issues are evidence, not canon. Main repo files after review are stronger. | CANON_BEHAVIOR | P0 | SOURCE_REGISTRY.yaml / MEMORY_ROUTING.yaml | candidate |

## Active blocker

Runner PR publishing from issue worktrees is blocking normal delivery. Do not create more Runner content tasks for this path until the publisher route is repaired or a safe manual connector route is used.

## Next audit steps

1. Deduplicate Batch 6 against COMMANDS.yaml and MEMORY_ROUTING.yaml.
2. Promote accepted command meanings into COMMANDS.yaml.
3. Promote memory routing and adaptive rules into MEMORY_ROUTING.yaml.
4. Promote project priority and strategy separation into WORKING_STRATEGY and per-project STRATEGY.md files.
5. Promote assistant startup behavior into adapters/chatgpt/START_HERE.md.
6. Keep private project details out of public GitHub.