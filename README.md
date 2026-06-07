# Skeleton

Skeleton is an external model-neutral control layer for LLM-assisted work.

Skeleton means:

1. Load exact context from a declared manifest.
2. Select exact mode from declared commands.
3. Route memory by trust level and privacy class.
4. Separate boot, project, audit, recovery, code-task, and canon-write work.
5. Enforce read-before-write for durable changes.
6. Produce verifiable reports.
7. Keep ChatGPT, Runner, Codex, Gemini, and Jeeves in separate role contracts.

## Repository identity

```text
alanua/Skeleton = Skeleton Core repository.
alanua/jeeves = Jeeves runtime/product repository and historical migration source.
ChatGPT Exoskeleton = historical prototype and evidence source.
Jeeves = separate future assistant/product/runtime.
```

## Current status

```text
Status: ACTIVE_CONTROLLED_BOOTSTRAP
Active route: BOOT_MANIFEST.yaml
Current entrypoint: BOOT_MANIFEST.yaml
```

## Core rule

```text
Manifest controls behavior.
Markdown explains behavior.
Tooling verifies behavior.
Tests preserve behavior.
```

## Project files

```text
PROJECT_MANIFEST.yaml = canonical project identity.
STATE.yaml = handoff state (state_role: handoff_not_canon_truth).
```

## Public-safe Aufmass docs

```text
docs/AUFMASS_PRIVATE_WORKSPACE_CONTRACT.md = minimal private workspace contract for bounded private pilots.
docs/AUFMASS_PRIVATE_PILOT_PROTOCOL.md = public/private boundary for stage 1 private pilots.
docs/AUFMASS_SOURCE_PACK.md = source pack manifest intake checkpoint.
```
