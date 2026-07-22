# Cognee Memory Projection

Cognee is introduced only as an optional, derived semantic projection. SQLite remains the canonical store, and `MemoryGateway` remains the normal policy and write boundary. The Cognee adapter has no canonical write, delete, mutation, import, or self-improvement surface.

## Boundary

- Optional dependency group: `cognee = ["cognee==1.4.0"]`.
- Offline source publication does not install or import Cognee.
- `CogneePackageBackend` only probes package availability with `importlib.util.find_spec`.
- Real package execution, model-backed indexing, recall, and forget remain blocked behind a later runtime slice. Even when `cognee==1.4.0` is installed and `runtime_enabled=true`, package-backed `project`, `recall`, and `forget_projection` fail closed with `COGNEE_RUNTIME_NOT_IMPLEMENTED`; package-backed health returns typed `UNAVAILABLE`.
- `self_improvement` is not supported and no `improve` method is exposed.

## Scope Rules

Every projection and recall request must include exact `project_id` and `dataset_id`. Empty, wildcard, ambiguous, foreign, cross-project, and cross-dataset scope fails closed. Recall never broadens to all projects or all datasets.

## Projection Events

Projection events are derived from canonical memory and must include:

- positive `canonical_revision`;
- exact `canonical_ref`;
- canonical `content_hash`;
- separate deterministic `projection_text_hash`;
- bounded Unicode projection text;
- bounded provenance metadata.

The canonical `content_hash` does not need to equal the projection text hash. Projection text accepts Ukrainian, German, and arbitrary Unicode vocabulary. Control characters and oversize text are rejected; text is not filtered lexically.

## Receipts And Recall

Public receipts contain only status, aggregate counts, canonical revisions, hashes, and stable reason codes. They intentionally omit raw project IDs, dataset IDs, canonical refs, text, provenance, paths, and private values.

Private recall responses may include exact scope and bounded synthetic result metadata. Backend result scope, provenance hash, canonical hash, and revision binding are verified before returning. Projection and health freshness stay bound to the exact current canonical revision, while individual recall results may reference unchanged canonical facts written at any positive revision less than or equal to that current revision. Future, zero, negative, malformed, foreign-scope, and otherwise unbound result revisions fail closed.

Revision `0` is valid for health and recall of an empty store. Projection events require revision `1` or later. Stale projections fail visibly with `PROJECTION_STALE` before backend recall is called.

## Forget

`forget_projection` is adapter-local projection forget only. It removes derived backend state for one exact project/dataset scope and does not call `MemoryGateway`, SQLite, or any canonical mutation/delete API.
