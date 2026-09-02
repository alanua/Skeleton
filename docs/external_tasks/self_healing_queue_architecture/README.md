# Skeleton self-healing queue — external architecture review

This package is for an independent architecture review by Claude and Kimi.

Start here: `TASK.md`.

Goal: propose a strong modern architecture that makes the Skeleton execution queue self-healing, continuously productive, deterministic, auditable, privacy-safe, and able to survive executor/model/provider/runtime failures without manual babysitting or false completion.

This is an architecture review only. Do not mutate production, secrets, devices, Runner state, protected files, or GitHub labels. Do not propose a second Scheduler/Runner/control plane.

Current repository baseline for this review:

- repository: `alanua/Skeleton`
- public repository
- main SHA at package creation: `60dda6337da32c9f80d1f3a5ac19ae2b9008e507`
- branch containing this review package: `external/self-healing-queue-architecture-review-20260817`

Read in this order:

1. `TASK.md` — exact question and required deliverable.
2. `CURRENT_STATE.md` — what already exists and what is failing now.
3. `FAILURE_CASES.md` — real observed failure patterns that the design must eliminate.
4. `ARCHITECTURE_CONSTRAINTS.md` — non-negotiable Skeleton invariants and safety boundaries.
5. `SOURCE_INDEX.md` — canonical code/docs/issues to inspect.
6. `DELIVERABLE_CONTRACT.md` — exact output structure expected from you.

Return your answer as one Markdown document. Preferred names if you can write/fork/PR:

- `results/CLAUDE.md`
- `results/KIMI.md`

If you cannot write to GitHub, return the complete Markdown to the operator, preserving the requested section headings from `DELIVERABLE_CONTRACT.md`.

Important: criticize the current design where needed. Do not optimize for preserving existing code. Preserve only the stated authority, privacy, safety, and audit invariants. The requested result is an architecture that can realistically bring the queue to approximately 9/10 operational reliability, not a cosmetic patch list.
