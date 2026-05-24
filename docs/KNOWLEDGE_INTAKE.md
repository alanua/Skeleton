# Knowledge Intake

Knowledge intake is the durable review path for recovered ideas, future
transcripts, articles, reports, news, and source notes. It defaults to BZ
knowledge intake, not KOD and not Runner execution.

The intake path is for preserving public-safe ideas so they are not lost in chat
memory. It does not make the idea canon, does not create live work, does not
activate runtime behavior, and does not authorize side effects.

## Classification

Each incoming item should be classified before any durable write:

- `CANON`: already-approved canon material with evidence.
- `REVIEW`: plausible direction that needs critique before adoption.
- `BACKLOG`: useful future idea with no near-term commitment.
- `REJECTED`: unsafe, out of scope, or explicitly declined.
- `PRIVATE`: private or sensitive material that must not be published.
- `TEMPORARY`: short-lived context that should not become durable memory.

`CANON_CANDIDATE` may be used in `projects/skeleton/REVIEW_QUEUE.yaml` for a
public-safe item that looks suitable for canon promotion but still requires the
normal BZ approval flow.

`TEMPORARY_CONTROL` may be paired with `REVIEW` for uploaded correction or
work-plan control notes that need to be preserved for reconciliation. These
entries remain temporary review material, not canon, not roadmap, and not active
Runner work.

## Storage Rules

Public-safe durable items go to `projects/skeleton/REVIEW_QUEUE.yaml` first.
`REVIEW_QUEUE` entries are not canon by default and remain
`not_canon_until_promoted` unless they are explicitly rejected.

Private data goes to `private_memory` only, not GitHub. Private data includes
personal context, private transcripts, private operational notes, client data,
home details, credentials, and anything that would be inappropriate for the
public repository.

Secrets never belong in chat, GitHub, or plain Drive. Secrets must use a local
encrypted store or a proper secret manager route.

## Route Rules

Transcripts, articles, reports, source notes, and recovered chat memory default
to BZ knowledge intake. They must not become KOD work or Runner tasks unless the
operator explicitly selects that active route.

The plus command continues the active route only. If the active route is
ambiguous, the system must not create a Runner task. It should ask for route
clarification or place public-safe durable material into BZ intake review.

## Jeeves Boundary

The Skeleton review queue may hold public-safe Jeeves ideas because Skeleton
controls the intake mechanism. That does not make Jeeves an active Skeleton
adapter. Jeeves remains a separate future assistant product and runtime unless a
future approved task creates a specific integration boundary.

Jeeves entries in the queue are review or backlog material. They do not activate
Jeeves runtime work, create implementation tasks, or promote Jeeves ideas to
canon automatically.

## Safety Gates

Knowledge intake must preserve approval gates. No agent or route may perform
autonomous self-modification. Merge, deploy, secrets, runtime, execution-mode,
and canon changes require explicit operator approval.

Dangerous actions, private-environment control, and credential handling require
separate approved safety designs before any implementation or runtime use.

## Promotion To Canon

Promoting a `REVIEW_QUEUE` entry to canon requires:

1. Critique of the item, including conflicts, scope, risk, and existing matches.
2. A PatchPlan describing the exact canon write.
3. Explicit operator approval.
4. The approved write to the canon target.
5. Verification that the write matches the approval and does not publish private
   data or secrets.

Until that sequence is complete, `REVIEW_QUEUE` material is evidence for review,
not canon truth.
