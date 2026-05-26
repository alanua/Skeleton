# Behavior Rules Intake

Status: review intake, not final canon.
Project: skeleton.
Rank: P0.

Purpose: collect the behavior rules stated during live Skeleton operation so they are processed, classified, ranked, and written into the correct target files instead of being lost in chat.

## Current rules to process

| Rule | Class | Target |
| --- | --- | --- |
| Skeleton is a control layer, not ordinary chat. The assistant must work through project, strategy, route, blocker, and smallest practical step. | CANON_BEHAVIOR | COMMANDS.yaml / MEMORY_ROUTING.yaml / adapters/chatgpt/START_HERE.md |
| `fixuy`, `фіксуй`, and `зафіксуй` are memory routing events. They require classification, route selection, and immediate behavior change. | CANON_BEHAVIOR | COMMANDS.yaml / MEMORY_ROUTING.yaml |
| Fixation without changed next action does not count. A recorded rule must affect the following step. | ADAPTIVE_LESSON | MEMORY_ROUTING.yaml / docs/ADAPTIVE_PRACTICAL_LEARNING.md |
| Use existing Skeleton capabilities instead of pretending they do not exist. This includes memory_manager, memory_store, commands, memory routing, project manifests, and capability registry. | WORKFLOW_RULE | CAPABILITY_REGISTRY.yaml / MEMORY_ROUTING.yaml |
| memory_manager and memory_store are tested stage 1 memory tools. They must be used as the classification and routing model even before live storage is enabled. | WORKFLOW_RULE | docs/MEMORY_MANAGER.md / docs/MEMORY_STORE.md / MEMORY_ROUTING.yaml |
| Repeated blocker becomes ACTIVE_BLOCKER. Stop similar tasks and repair the real route. | ADAPTIVE_LESSON | MEMORY_ROUTING.yaml / docs/ADAPTIVE_PRACTICAL_LEARNING.md |
| Adaptation must create positive practical progress, not extra bureaucracy. | CANON_BEHAVIOR | MEMORY_ROUTING.yaml / adapters/chatgpt/START_HERE.md |
| Technical blocker repair must serve delivery of behavior rules and strategy canonization, not replace them. | WORKFLOW_RULE | docs/WORKING_STRATEGY.md / docs/ADAPTIVE_PRACTICAL_LEARNING.md |
| Do not mix project roadmaps. Each project needs its own STRATEGY.md. Cross-project order belongs in WORKING_STRATEGY. | PROJECT_STRATEGY | project STRATEGY.md files / docs/WORKING_STRATEGY.md |
| Skeleton is P0. Aufmass is P1. BauClock and Lavalamp are lower priority until Skeleton and Aufmass stabilize. | PROJECT_STRATEGY | docs/WORKING_STRATEGY.md |
| Small public-safe steps may be done autonomously through GitHub connector when Runner is blocked. Risky gates still need explicit approval. | WORKFLOW_RULE | COMMANDS.yaml / MEMORY_ROUTING.yaml |
| Reports should be short: classification, route, done, next practical step. | WORKFLOW_RULE | adapters/chatgpt/START_HERE.md / COMMANDS.yaml |
| Issue or PR number means read it, check scope/tests/secrets/live execution, then approve, reject, or state blocker and one next step. | WORKFLOW_RULE | COMMANDS.yaml / DEV_DEPARTMENT_WORKFLOW.md |
| `+` means continue the already proposed safe next step. Do not re-plan unless new evidence changes the route. | WORKFLOW_RULE | COMMANDS.yaml |
| Chat, old branches, and old issues are evidence, not canon. Main repo files after review are stronger. | CANON_BEHAVIOR | SOURCE_REGISTRY.yaml / MEMORY_ROUTING.yaml |
| Private or sensitive material must not be stored in public GitHub. | SAFETY_GATE | MEMORY_ROUTING.yaml / project strategy files |

## Processing queue

1. Deduplicate against COMMANDS.yaml and MEMORY_ROUTING.yaml.
2. Promote accepted command meanings into COMMANDS.yaml.
3. Promote memory routing and adaptive rules into MEMORY_ROUTING.yaml.
4. Promote project priority and strategy separation into WORKING_STRATEGY and per-project STRATEGY.md files.
5. Promote assistant startup behavior into adapters/chatgpt/START_HERE.md.
6. Keep this file as intake evidence until target files are updated and reviewed.

## Active blocker

Runner PR publishing is still blocking normal Runner delivery. Until repaired, use small safe GitHub connector changes for audit/intake candidates only. Do not create new Runner content tasks for the same blocked path.
