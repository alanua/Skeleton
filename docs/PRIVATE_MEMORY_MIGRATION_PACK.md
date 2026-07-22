# Private Memory Migration Pack

This contract defines a public-safe, review-only migration pack for private-memory reconciliation. It rebuilds the complete base-to-head contract for PR #1890 while intentionally excluding raw private values, payloads, local paths, credentials, customer data, and private fixtures.

No private import or runtime mutation occurred. The pack is metadata-only evidence for review and cannot activate namespaces, route traffic, mutate canonical memory, delete records, back up stores, deploy services, or run discovery against private sources.

## Contract Sequence

The review flow is strictly ordered:

1. `source_inventory` lists only stable source metadata under `PUBLIC_SAFE_METADATA_ONLY`.
2. `extraction_manifest` proposes bounded records with #1753 metadata vocabulary: `PRIVATE`, `RESTRICTED`, `SECRET_REF_ONLY`, `OPERATOR_CONFIRMED`, `APPROVED_CANON`, `SOURCE_FACT`, `OPERATIONAL`, `DERIVED`, `PROVISIONAL`, `HIGH`, `MEDIUM`, and `LOW`.
3. `reconciliation_report` compares current and proposed hashes for a target canonical revision using `add`, `update`, `tombstone`, `conflict`, and `unchanged`.
4. `approval_packet` binds upstream hashes, target revision, namespace policy review, requested operations, operator approval evidence, and packet idempotency.
5. Exact readback must confirm the approved packet hash, every requested operation hash, canonical revision expectation, and projection freshness before any future import-capable implementation could be considered.

The candidate namespace strategy is review-only. Namespace policy remains unchanged, and namespace activation is explicitly out of scope for this pack.

## Fail-Closed Gates

The pack fails closed when any gate cannot be proven:

- Revision gate: `target_canonical_revision` and per-record `canonical_revision_expected` must match the reviewed state.
- Hash gate: source inventory, extraction manifest, reconciliation report, approval packet, source, content, proposed, current, and idempotency hashes must bind the reviewed artifacts.
- Namespace gate: `namespace_proposal_ref` and `namespace_policy_review_ref` are review references only and do not activate routing or policy.
- Approval gate: approved packets require non-null `approved_by_ref` and `approved_at`; revoked packets require non-null `revoked_at` and `revocation_ref`.
- Idempotency gate: packet-level and operation-level idempotency keys must be present before any future import path can reason about duplicate submissions.
- Projection freshness gate: projection indexes must be fresh to the target canonical revision before review can proceed.
- Readback gate: exact readback must return the same hashes and operation references that were approved.

## Public-Safe Boundaries

All examples and tests are synthetic. Hashes are synthetic `sha256:` references, IDs are bounded metadata refs, timestamps are non-secret review timestamps, and source references contain only `stable_source_id` plus `source_hash`.

The schemas reject unknown top-level and nested fields. They also reject URL and path-shaped refs, and they provide no fields for raw values, content, payloads, local paths, credentials, or secrets.

## Required Contract Sections

The four schemas preserve the required sections:

- Source inventory: `schema`, `inventory_id`, `generated_at`, `privacy_boundary`, `project_ref`, `domain_ref`, `sources`, `inventory_hash`; each source requires stable source metadata, privacy class, integrity hash, retention class, extraction class, and authority with `authority_class`, `authority_ref`, and `approval_ref`.
- Extraction manifest: `schema`, `manifest_id`, `generated_at`, `source_inventory_ref`, `source_inventory_hash`, `records`, `manifest_hash`; each record requires safe refs, #1753 privacy and authority enums, source refs, nullable temporal fields, nullable revision, supersession, correction, tombstone links, confidence, approval ref, and content hash.
- Reconciliation report: `schema`, `report_id`, `generated_at`, upstream inventory and manifest refs/hashes, `target_canonical_revision`, aggregate counts, records, and report hash.
- Approval packet: `schema`, `packet_id`, `packet_hash`, upstream hashes, target revision, namespace policy review ref, `skeleton.memory_gateway.compatible_private_import.review_only.v1`, requested operations, aggregate counts, operator approval, and idempotency.

This contract reuses the exact #1753 vocabulary instead of replacing it with parallel legacy values. It remains draft review material for PR #1890 and is unmerged pending final review.
