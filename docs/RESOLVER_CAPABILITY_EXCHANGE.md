# Resolver Capability Exchange

Resolver fixes move through a controlled capability lifecycle instead of landing as isolated runtime patches.

Status flow: discovered, researching, implemented, tested, approved, canary, active, degraded, disabled, rolled_back.

## Failure Intake

Resolver failures are normalized into sanitized evidence containing only host, failure class, bounded diagnostics, runtime version, adapter chain, and negative knowledge. Cookies, authorization headers, tokens, signed media URLs, private headers, and secrets are removed before persistence or task creation.

Transient failures (`network`, `timeout`, `rate_limit`, `origin_protected`, `browser_challenge`) create cooldown and negative-knowledge records only. Structural failures (`parser_failure`, `schema_mismatch`, `runtime_integration_missing`) must cross a configured threshold before a single deduplicated Runner task is prepared.

Resolver research order is fixed: documented API, structured data, standard embed, HLS/DASH, known player adapter, rendered DOM, site-specific resolver, graceful failure.

## Registry And State

Stable capability identity belongs in DEVICE_REGISTRY-compatible canon: `capability_id`, semantic version, supported hosts, package hash, manifest hash, and registered deploy/verify/rollback operation IDs.

Live installation health belongs in STATE_DATABASE-compatible state: node ID, active version, package hash, rollback version, deployment receipts, last success, and last failure.

## Packaging

Immutable packages contain:

- `manifest.json`
- `dependency-lock.json`
- `code/`
- `fixtures/`
- `tests/`
- `operations/deploy.json`
- `operations/verify.json`
- `operations/rollback.json`
- `attestation.json`

The package is SHA-256 pinned at install time and the manifest is independently hashed. Nodes exchange package identity by `capability_id`, version, and hash; they do not copy mutable source files from each other.

## Rollout

Deployment stages are compatibility check, dry run, canary, activation, and rollback. Operations use registered Skeleton Home Edge execution requests, preserving existing approval and audit behavior.

Production activation is blocked until Skeleton approval is present and independent verification reports `sent`, `accepted`, `applied`, `physically_verified`, and `application_verified`.

Rollback restores the recorded prior version and requires runtime health verification.

## Failure Recovery

Negative knowledge is exchanged as public-safe facts: known failed methods, cooldowns, false positives, provider limitations, failure class, and host. Secrets and request credentials are never included. A compatible node may discover and install the same immutable package by capability ID, version, and hash after its own compatibility check and approval path.
