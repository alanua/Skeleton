# Migration from alanua/jeeves

Status: DRAFT_MIGRATION_NOTE
Scope: separation of Skeleton Core from historical Jeeves repository

## Repository split

```text
alanua/Skeleton = Skeleton Core repository.
alanua/jeeves = Jeeves runtime/product repository and historical migration source.
```

## Migration means

1. Build Skeleton v2 in `alanua/Skeleton`.
2. Treat old ChatGPT Exoskeleton material in `alanua/jeeves` as historical source evidence.
3. Extract only reviewed Skeleton rules, schemas, workflows, and tests.
4. Keep Jeeves runtime/product work in `alanua/jeeves`.
5. Keep private data out of public repositories.
6. Keep secrets in local encrypted storage or a secret manager.

## Current v2 bootstrap route

```text
BOOT_MANIFEST.yaml
-> COMMANDS.yaml
-> MODES.yaml
-> SOURCE_REGISTRY.yaml
-> MEMORY_ROUTING.yaml
-> PROJECT_INDEX.yaml
-> STATUS_CODES.yaml
```

## Migration stages

1. Bootstrap manifest, schemas, and tests in `alanua/Skeleton`.
2. Add project manifests and state handoff files.
3. Add adapter contracts for ChatGPT, Claude, Gemini, Runner, and Codex.
4. Add boot loader and validators.
5. Produce bridge instructions for old `alanua/jeeves` ChatGPT Exoskeleton route.
6. Switch active Skeleton work to `alanua/Skeleton` after explicit Oleksii approval.

## Archive rule

Old startup, diary, continuity, recovery, and runbook files remain evidence until classified.

Classification means:

1. CONFIRMED_CANON
2. NEEDS_REVIEW
3. ARCHIVE_REFERENCE
4. ON_DEMAND_REFERENCE
5. OUTDATED_REJECTED
6. PRIVATE_ROUTE

## Active startup route

Skeleton v2 startup route is the manifest route declared in `BOOT_MANIFEST.yaml`.
