# Production Engineering Quality Pipeline

Phase 1 records only normalized task intent, declared repository scope, and
head-bound validation evidence. It does not synthesize observed diff impact,
touched-file reality, architecture approval, or runtime proof.

Current Runner task shapes may declare public-safe policy metadata, private or
local-only privacy boundaries, and composite privacy boundaries. Normalization
classifies those boundaries into a public-safe or protected-private class and
only exposes public-safe policy tokens in public summaries.

Allowed files are declared authorization scope. Exact repository-relative paths
and bounded repository-relative `/**` glob scopes such as `tests/**` are accepted.
Absolute paths, traversal, control characters, and unbounded wildcard scopes are
rejected.

Phase 1 quality evidence remains bound to an exact repo, base, and head. If the
head changes, the evidence is invalidated. `ARCHITECTURE_GREEN` and
`RUNTIME_PROVEN` remain unreachable in Phase 1.
