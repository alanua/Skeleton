# Task

Produce a complete, reviewable solution that makes Skeleton Universal Memory installable and runnable in a bounded local environment.

The current source implementation exists. The unresolved operational target is GitHub issue #1904, with the latest activation attempt in #1957 blocked by `ollama_unavailable`.

Required work:

1. Audit the current `main` implementation and the files listed in ALLOWED_FILES.md.
2. Reproduce or explain the `ollama_unavailable` activation failure using public-safe/synthetic inputs only.
3. Design the smallest correct repair. Do not replace the existing architecture.
4. Implement the repair as patches against the exact audited Skeleton commit.
5. Provide an idempotent installation and startup procedure for a local-only stack.
6. Provide preflight, health, synthetic end-to-end smoke, degraded-mode, rollback, and uninstall/disable procedures.
7. Prove that canonical SQLite remains usable if any derived projection is absent or unhealthy.
8. Prove that no cloud endpoint, telemetry, secret, private value, or private path is required or exposed.
9. Do not claim production activation. Return a package for Skeleton review and controlled execution.

The package must contain:

- `design.md`
- `architecture.md`
- `current_state_audit.md`
- `installation.md`
- `runbook.md`
- `rollback.md`
- `code.patch`
- `tests.patch`
- `test_report.md`
- `review.md`
- `manifest.json` with base commit, file hashes, commands, tool/dependency versions, and known limitations

Return only the repository/Gist URL, immutable commit SHA, and a summary of at most five lines.
