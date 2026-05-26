# Knowledge Intake

Knowledge intake is the durable review path for recovered ideas, future transcripts, articles, reports, news, pasted research, sourcepacks, and external analysis. It defaults to BZ knowledge intake, not KOD and not Runner execution.

The intake path preserves public-safe ideas so they are not lost in chat memory. It does not make an idea canon, does not create live work, does not activate runtime behavior, and does not authorize side effects.

## Runtime Contract

For transcript, video text, article, news, external report, sourcepack, or assistant-output analysis, the active route is:

```text
mode: BZ / knowledge intake
target_project: detected project
write_gate: required
runner: no
codex: no
github_issue: no
```

The assistant must first state the detected BZ route, then classify and critique the material, compare it with existing Skeleton canon and review queue entries, propose storage, and wait for explicit approval before writing.

The `+` command continues the active route. If the active route is BZ, `+` continues BZ classification, critique, PatchPlan, or approved write. If the active route is ambiguous, the assistant must not create a Runner task.

## Mandatory Preflight Before Writing

Before any BZ knowledge-intake write, the assistant must check the existing system first:

```text
BOOT_MANIFEST.yaml
COMMANDS.yaml
MEMORY_ROUTING.yaml
SOURCE_REGISTRY.yaml
PROJECT_INDEX.yaml
docs/KNOWLEDGE_INTAKE.md
projects/skeleton/REVIEW_QUEUE.yaml
open and closed issues/PRs related to knowledge intake, review queue, idea backlog, and canon write
```

Prior issues and closed PRs can contain the active contract even when a file search result is incomplete.

## Canonical Storage Location

Public-safe durable intake goes to exactly one central file first:

```text
projects/skeleton/REVIEW_QUEUE.yaml
```

Standalone intake batch files under `projects/skeleton/review_queue/` are not the active storage mechanism. Do not create them unless a future approved schema introduces an explicit index in `projects/skeleton/REVIEW_QUEUE.yaml` and tests for that index.

`REVIEW_QUEUE` entries are not canon by default and remain `not_canon_until_promoted` unless they are explicitly rejected.

## Classification

Each incoming item must be classified before durable write:

- `CANON`: already-approved canon material with evidence.
- `CANON_CANDIDATE`: public-safe item that appears suitable for canon promotion but still requires the full BZ approval flow.
- `REVIEW`: plausible direction that needs critique before adoption.
- `BACKLOG`: useful future idea with no near-term commitment.
- `REJECTED`: unsafe, out of scope, duplicate, wrong, or explicitly declined.
- `PRIVATE`: private or sensitive material that must not be published.
- `TEMPORARY`: short-lived context that should not become durable memory.
- `TEMPORARY_CONTROL`: uploaded correction or work-plan control notes that need preservation for reconciliation but are not canon, not roadmap, and not active Runner work.

## Storage Rules

Public-safe durable items go to `projects/skeleton/REVIEW_QUEUE.yaml` first.

Private data goes to `private_memory` only, not GitHub. Private data includes personal context, private transcripts, private operational notes, client data, home details, and anything inappropriate for the public repository.

Sensitive access material must never be placed in chat, GitHub, or plain Drive. It must use a protected local route or approved secret-management route.

Temporary noise goes to no-persist. Archive or evidence material goes to archive/history/recovery routes, not default canon.

## Route Rules

Transcripts, articles, reports, source notes, and recovered chat memory default to BZ knowledge intake. They must not become KOD work, Runner tasks, Codex prompts, GitHub issues, or PRs unless the operator explicitly selects that execution route.

BZ writes may update the review queue directly after explicit approval. They must not silently create execution work.

## Jeeves Boundary

The Skeleton review queue may hold public-safe Jeeves ideas because Skeleton controls the intake mechanism. That does not make Jeeves an active Skeleton adapter. Jeeves remains a separate future assistant product and runtime unless a future approved task creates a specific integration boundary.

Jeeves entries in the queue are review or backlog material. They do not activate Jeeves runtime work, create implementation tasks, or promote Jeeves ideas to canon automatically.

## Safety Gates

Knowledge intake must preserve approval gates. No agent or route may perform autonomous self-modification. Merge, deploy, runtime, execution-mode, sensitive-access, and canon changes require explicit operator approval.

Dangerous actions, private-environment control, cloud fallback for private data, and tool-bearing local models require separate approved safety designs before implementation or runtime use.

## Promotion To Canon

Promoting a `REVIEW_QUEUE` entry to canon requires:

1. Critique of the item, including conflicts, scope, risk, duplicates, and existing matches.
2. A PatchPlan describing the exact canon write.
3. Explicit operator approval.
4. The approved write to the canon target.
5. Verification that the write matches the approval and does not publish private or sensitive material.

Until that sequence is complete, `REVIEW_QUEUE` material is evidence for review, not canon truth.

## Operational Correction

A prior mistake created standalone files under `projects/skeleton/review_queue/`. That was the wrong storage pattern. The corrected rule is central-first storage in `projects/skeleton/REVIEW_QUEUE.yaml` with tests that block unindexed standalone batch files.
