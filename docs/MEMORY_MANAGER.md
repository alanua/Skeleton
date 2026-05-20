# Memory Manager

`core/memory_manager.py` is stage 1 only: a local deterministic dry-run routing
contract. It classifies a `MemoryRecord` and returns a `MemoryRouteResult`. It
does not write to GitHub, Drive, files, databases, subprocesses, network APIs,
secret stores, or any other live destination.

## Memory Types

- `weak_chat_memory`: weak cache material from conversational memory. ChatGPT
  memory is always treated as weak cache, not canon.
- `project_state`: project-local state that belongs on the project state route.
- `canon_candidate`: public-safe durable candidate that still requires operator
  approval before it can become canon.
- `confirmed_canon`: public-safe canon that routes to confirmed canon only after
  explicit operator approval.
- `private_sensitive`: private or sensitive material that must never route to
  public GitHub.
- `rejected_outdated`: outdated, stale, rejected, or unsupported material kept
  only on the rejected archive route.

## Trust Levels

The memory manager records `trust_level` as input context and includes the source
in its audit summary. Stage 1 does not promote trust into authority. Current
user instructions and boot/canon sources still outrank weak cache memory under
the repository source-routing rules.

## Routing Table

| Memory type | Target route | Approval gate |
| --- | --- | --- |
| `weak_chat_memory` | `weak_cache` | None |
| `project_state` | `project_state` | None |
| `canon_candidate` | `github_canon_candidate` | Operator approval required |
| `confirmed_canon` | `github_confirmed_canon` | Explicit operator approval required |
| `private_sensitive` | blocked from public GitHub | Never public |
| `rejected_outdated` | `rejected_archive` | None |

`canon_candidate` records must be `public_safe` before they can route to the
GitHub canon-candidate target. The dry-run result marks
`requires_operator_approval` as true; a later stage must still collect approval
before any live write exists.

`confirmed_canon` records must be `public_safe` and `operator_approved` before
they route to `github_confirmed_canon`.

## Approval Gates

The stage 1 record shape includes three deterministic gate fields:

- `public_safe`: true only when the content is suitable for a public GitHub
  route.
- `critique_present`: true only when a required critique/review exists.
- `operator_approved`: true only when the operator has explicitly approved the
  approval-gated route.

The route result reports `status` as `accepted` or `blocked`, a `target_route`,
an optional `blocked_reason`, `requires_operator_approval`, and an
`audit_summary`.

## Private/Public Boundary

`private_sensitive` records never route to public GitHub. A canon record that is
not `public_safe` is blocked from GitHub canon routes. Stage 1 has no fallback
storage implementation for private data; it only reports the safe routing
decision.

## Canon And Instruction Critique Rule

Canon or instruction changes require critique plus operator approval. If
`changes_canon_or_instruction` is true and `critique_present` is false, routing
blocks before any canon target can be accepted. Confirmed canon additionally
requires `operator_approved` to be true.

## Stage 1 Dry Run

Stage 1 is intentionally side-effect free. It performs no live GitHub API calls,
no Drive calls, no local file writes, no database writes, no subprocess
execution, no network access, and no secret handling. Any future live promotion
requires a separate operator-approved stage with explicit review of side
effects.
