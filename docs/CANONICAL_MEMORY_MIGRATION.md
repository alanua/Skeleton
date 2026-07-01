# Canonical Memory Migration

Issue 1194 prepares the first public-safe canonical memory migration layer for an approved operator preference candidate.

This layer starts from one approved manifest:

- canonical target namespace: `skeleton.operator_preferences`
- scope: `global_operator_working_style`
- initial key: `fast_autonomous_execution_v1`
- record type: `operator_working_style_preference`
- authority: `candidate_manifest_only`
- provenance: approved GitHub issue comment reference `4846756659`
- privacy classification: `public_safe_operator_preference`

The candidate binds the exact 12-rule specification approved in comment `4846756659`: fast obvious-next-step progress, independent action inside granted authority, low procedural overhead, useful work over paperwork, real-blocker-only stopping, concise result-focused updates, explicit next-action/operator-action status fields, safe read-only parallelization, incremental use of verified memory layers, canonical SQLite/Memory Gateway authority, Graphify relationship recall, and non-authoritative MemPalace semantic recall with exact canonical confirmation.

Each accepted rule has one fixed ID, category, and statement. Validation rejects changed statements even when the integrity hash is recomputed, duplicate IDs or categories, missing or extra rules, and unsupported fields at the top level or inside provenance, supersession, record, and rule objects. The JSON schema applies equivalent exact-count, uniqueness, and fixed-rule bounds.

The manifest does not include raw chat, transcript fields, private values, local paths, secrets, customer data, environment values, or direct SQLite write intent. Its integrity hash is computed from deterministic JSON over the manifest with `integrity_hash` removed.

The first bounded local import path is implemented but must only be called with a trusted, caller-injected `SkeletonMemory` instance. Callers cannot provide a database path, SQL, command, environment, namespace, scope, key, or version. The sole gateway command is `skeleton.memory.import_canonical_manifest`, and it accepts only the exact approved manifest.

The import path validates the merged manifest with the canonical-memory manifest validator, then additionally requires exact approval provenance `issue-1194-comment-4846756659`, namespace `skeleton.operator_preferences`, scope `global_operator_working_style`, key `fast_autonomous_execution_v1`, version `1`, candidate-only authority, and the approved integrity hash.

Before mutation, the importer opens a transaction and records a recoverable pre-import snapshot marker in the same SQLite store. It assigns a monotonic canonical revision, preserves provenance, version, supersession, integrity hash, created revision, imported-at metadata, and `authoritative=true`, then reads back through `MemoryGateway.lookup_exact`. The read-back must match the byte-equivalent normalized manifest JSON, integrity hash, current revision, and authoritative status before commit.

Repeated import of the same manifest is idempotent and returns the existing canonical revision. Changed manifest content, recomputed statement hashes, missing approval provenance, namespace/scope/key/version mismatches, unsupported fields, conflicting existing versions, unavailable snapshots, write failures, and read-back mismatches fail closed and roll back the transaction.

The public receipt is aggregate-only: status, idempotency classification, namespace token, scope token, key token, version, canonical revision, integrity hash, snapshot status, read-back status, rollback status, and authoritative boolean. It does not include database paths, SQL, raw transcripts, local paths, secrets, customer data, or the manifest body.

This task does not execute a live/runtime import and does not activate operator preferences outside isolated tests. A fresh ChatGPT review is required before any runtime handler or local activation.
