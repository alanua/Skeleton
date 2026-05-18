# Adapter Contracts

Skeleton uses one shared entrypoint:

```text
BOOT_MANIFEST.yaml
```

adapters do not define boot routing.

Each adapter attaches to Skeleton through `BOOT_MANIFEST.yaml` and then stays inside its own bounded role contract.

## Adapter map

- `adapters/chatgpt/START_HERE.md` -> conversational planner/operator interface
- `adapters/claude/START_HERE.md` -> critique/review/planning adapter
- `adapters/gemini/AUDITOR_CONTRACT.md` -> audit-only reviewer
- `adapters/codex/EXECUTOR_CONTRACT.md` -> bounded executor for GitHub/code tasks
- `adapters/runner/RUNNER_CONTRACT.md` -> execution environment for bounded tasks

## Shared boundary

Adapters attach to Skeleton through the manifest, not through separate boot surfaces.

They may describe role limits, review limits, and execution limits, but they do not replace project manifests, state files, or runtime logic.
