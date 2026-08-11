# Project Memory Access

`core.project_memory_adapter.ProjectMemoryAdapter` is the universal adapter layer for
domain projects that need memory context or want to propose memory changes. It keeps
Skeleton as the model-neutral control layer by translating typed project requests into
the existing `MemoryGateway` contract.

The adapter is intentionally not project-specific. Gewerbe, Travel, and future modules
bind the same contract with:

- `project_id`: the domain project scope, for example `travel` or `gewerbe`
- `namespace`: an existing `MemoryGateway` namespace, usually `skeleton` for shared
  project control metadata
- `privacy_boundary`: the active data boundary, for public tests this is
  `PUBLIC_SAFE_CODE_TESTS_ONLY`

No project code should read or write SQLite directly. Durable memory mutation remains
behind `MemoryGateway`; public project access can only create patch proposals.

## Request Contract

Requests use `schemas/project_memory_request.schema.json` and must include:

- `project_id`
- `namespace`
- `operation`
- `evidence`
- `classification`
- `privacy_boundary`
- `parameters`

Supported operations are:

- `read_context`
- `propose_fact`
- `propose_task`
- `propose_preference`
- `list_pending_review`

Supported candidate classifications are:

- `CANON`
- `REVIEW`
- `BACKLOG`
- `REJECTED`
- `PRIVATE`
- `TEMPORARY`

## Mutation Rules

`CANON`, `REVIEW`, and `BACKLOG` proposal operations are translated to
`memory.propose_patch`. The gateway preserves canonical namespace scoping,
exact-evidence checks, proposal idempotency, and audit receipts.

`CANON` and instruction-like changes are still proposals only. They require Oleksii
approval before durable canon import or promotion. The adapter receipt reports
`operator_approval_required: true` and `canonical_write_performed: false`.

`PRIVATE` candidates are proposal-only and may pass through `MemoryGateway` only when
the adapter binding, request `privacy_boundary`, and gateway capability token are all
`PRIVATE_RUNTIME_ONLY`. `PUBLIC_SAFE_CODE_TESTS_ONLY` and `SECRET_REFERENCE_ONLY`
boundaries block before the adapter calls the gateway, and the gateway independently
rejects `PRIVATE` proposal classifications unless its capability token is
`PRIVATE_RUNTIME_ONLY`. Private project facts, budgets, documents, watchlists,
identifiers, and secrets must stay in private runtime memory or secret-reference
stores, never in GitHub fixtures or public receipts.

`TEMPORARY` candidates return `TEMPORARY_ONLY` and are not sent to the gateway.
`REJECTED` candidates return `BLOCKED` and are not sent to the gateway.

## Receipts

Receipts use `schemas/project_memory_receipt.schema.json`. They are aggregate and
sanitized. They may contain stable refs, counts, gateway command names, revisions, and
reason codes. They must not contain raw proposed values or private document contents.

Stable reason codes include:

- `CONTEXT_READ_THROUGH_GATEWAY`
- `PENDING_REVIEW_LISTED_THROUGH_GATEWAY`
- `CANON_CHANGE_PROPOSAL_REQUIRES_OPERATOR_APPROVAL`
- `PROPOSAL_ALREADY_PENDING_REVIEW`
- `PRIVATE_RECORD_REQUIRES_PRIVATE_RUNTIME_BOUNDARY`
- `TEMPORARY_RECORD_NOT_DURABLE`
- `CANDIDATE_CLASSIFIED_REJECTED`

GitHub-safe material is limited to schemas, adapter code, documentation, tests, and
synthetic fixtures. Live private memory, Gmail, Drive, documents, SQLite files, and
runtime activation are outside this contract.
