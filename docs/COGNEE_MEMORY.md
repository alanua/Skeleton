# Cognee Semantic Memory Projection

This slice introduces a narrow, derived semantic projection boundary for Cognee.
It is not an authoritative memory store and it is not active at runtime by
default.

## Authority

Skeleton canonical memory remains authoritative. The Cognee projection may store
only bounded synthetic projection events that already carry:

- explicit `project_id` and `dataset_id`
- `canonical_revision`
- `canonical_ref`
- `content_hash`
- bounded public-safe projection text
- provenance metadata

Recall results are advisory. Every result is rebound to `project_id`,
`dataset_id`, `canonical_revision`, `canonical_ref`, `content_hash`, and
provenance metadata so callers can verify against canonical memory through the
normal exact-read path.

## Isolation

The adapter supports only explicit project and dataset identifiers. Empty,
wildcard, `all`, mismatched, and cross-project scopes fail closed with stable
reason codes such as `PROJECT_ID_AMBIGUOUS`, `DATASET_ID_AMBIGUOUS`, and
`CROSS_PROJECT_RECALL_FORBIDDEN`.

The default Cognee backend only imports the optional `cognee==1.4.0` package and
reports availability. Model-backed indexing and network-backed search are behind
the next activation gate. Tests use an injected disposable backend that stores
synthetic data in process memory only.

## Rebuild And Freshness

Projection freshness is revision-bound. Recall must present the current
canonical revision for the explicit project and dataset. If the adapter indexed a
different revision, recall fails with `PROJECTION_STALE`; callers must rebuild
the derived projection from approved bounded events before retrying.

## Failure Semantics

Visible public-safe reason codes are:

- `MEMORY_UNAVAILABLE`
- `PROJECT_ID_AMBIGUOUS`
- `DATASET_ID_AMBIGUOUS`
- `PROJECTION_STALE`
- `CROSS_PROJECT_RECALL_FORBIDDEN`
- `COGNEE_DEPENDENCY_UNAVAILABLE`
- `INVALID_PROJECTION_EVENT`
- `INVALID_RECALL_REQUEST`
- `CONTENT_HASH_MISMATCH`

Public receipts contain only status, aggregate counts, content hashes,
canonical revisions, project/dataset identifiers, and stable reason codes. They
do not include private values, raw paths, credentials, prompts, or unbounded
memory text.

## Delete And Forget

`forget_projection` deletes only adapter-local projection rows for the bound
project and dataset. It does not call `MemoryGateway`, canonical SQLite,
`PrivateMemoryStack`, or any canonical delete path. Canonical deletion remains
outside this slice.

## Runtime Activation Gate

Before production runtime activation, a separate reviewed change must add:

- an approved Cognee storage location scoped to disposable derived data
- explicit no-network or approved-network policy for the backend
- model/provider configuration that contains no secrets in receipts or logs
- integration through an existing Skeleton memory route without bypassing exact
  canonical reads
- tests proving no direct Cognee-to-canonical write, no `improve`, no automatic
  promotion, and no cross-project recall
