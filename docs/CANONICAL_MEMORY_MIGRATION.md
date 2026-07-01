# Canonical Memory Migration

Issue 1194 prepares the first public-safe canonical memory migration layer for an approved operator preference candidate.

This layer is intentionally manifest-only:

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

Runtime import remains gated because the existing Memory Gateway has no verified local import API with approval provenance, snapshot, integrity check, and rollback. The gateway route added for this layer only validates and reads back the manifest for operator review. It returns `authoritative: false` and does not write to canonical SQLite or activate the record as canonical exact memory.
