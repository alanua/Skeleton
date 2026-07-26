# Acceptance tests

Required evidence:

1. Exact base commit and clean patch applicability.
2. Fresh isolated install from documented prerequisites.
3. Idempotent second install.
4. Explicit diagnostics for missing Ollama binary, stopped service, unavailable loopback endpoint, missing model, and incompatible model.
5. Local-only provider enforcement and rejection of non-loopback endpoints.
6. Canonical SQLite write/read only through MemoryGateway.
7. Exact replay and idempotency conflict behavior.
8. Revision/history and crash recovery.
9. Projection outbox recovery after canonical commit.
10. Fresh Cognee recall with exact scope, revision, hashes, refs, and provenance.
11. MemPalace fallback only at the exact canonical revision.
12. Graphify included independently only when fresh.
13. Rebuild of all derived projections from canonical SQLite.
14. Namespace/project/dataset isolation.
15. Mandatory-memory fail-closed behavior.
16. Degraded mode preserving canonical SQLite when projections are unavailable.
17. 0600 temporary context outside the repository, removed on success, failure, timeout, and cancellation.
18. No private marker in stdout, stderr, receipts, logs, argv, environment dump, or repository files.
19. Bounded CPU, RAM, disk, and timeout behavior.
20. Rollback and disable procedure verified.
21. Focused tests and full Skeleton test suite.
22. `git diff --check` and changed-file allowlist proof.

A mocked-only proof is insufficient. Use temporary real SQLite plus actual local components where available, with synthetic data only. Where a dependency cannot run, report BLOCKED with an exact stable reason; do not fake PASS.
