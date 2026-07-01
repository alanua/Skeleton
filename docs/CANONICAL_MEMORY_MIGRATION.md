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

The approved durable rule set is encoded as separate bounded operating rules for fast autonomous progress, established approval boundaries, minimal issue churn, mandatory next-action and operator-action status fields, safe read-only parallelization, incremental use of verified memory layers, canonical SQLite authority, derived Graphify relationship use, non-authoritative MemPalace semantic use, and exact canonical confirmation.

The manifest does not include raw chat, transcript fields, private values, local paths, secrets, customer data, environment values, or direct SQLite write intent. Its integrity hash is computed from deterministic JSON over the manifest with `integrity_hash` removed.

Runtime import remains gated because the existing Memory Gateway has no verified local import API with approval provenance, snapshot, integrity check, and rollback. The gateway route added for this layer only validates and reads back the manifest for operator review. It returns `authoritative: false` and does not write to canonical SQLite or activate the record as canonical exact memory.
