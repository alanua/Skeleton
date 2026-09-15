# Skeleton Self-Knowledge

Skeleton self-knowledge stores public-safe topology facts that let runner code resolve verified host, repository, runtime, and entrypoint references deterministically.

The public contract is intentionally narrow:

- `TopologyFact` records contain stable IDs, roles, public fingerprints, opaque references, source/provenance, verified revision, verification time, and freshness state.
- Private paths, private roots, addresses, credentials, and live runtime values stay outside the public repository. When a private value is needed, the fact carries an `OPAQUE_PRIVATE_REF` such as `private-ref:workspace-root:v1`.
- `SelfKnowledgeResolver` selects only verified, current, non-superseded facts. Stale facts return a stale receipt and never route new work.
- Equal-authority facts with the same winning revision and verification time but different refs fail closed as `NEEDS_VERIFICATION`.
- Superseded facts remain available as historical provenance but cannot route new actions.
- Public receipts expose status, opaque refs, freshness, revision, timestamps, and provenance refs only.

This slice does not read from or mutate `MemoryGateway`, does not call devices or runtimes, and does not embed private machine paths in fixtures.
