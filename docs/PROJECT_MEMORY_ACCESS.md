# Project Memory Access

Project memory access is routed through `ProjectMemoryAdapter`, which calls
`MemoryGateway.execute` for proposal creation. It does not read SQLite, Graphify,
MemPalace, local files, or private fixtures directly.

`PRIVATE` records are accepted only when the request and gateway token both use
`PRIVATE_RUNTIME_ONLY`. The same record classification is blocked before any
gateway call under `PUBLIC_SAFE_CODE_TESTS_ONLY` and `SECRET_REFERENCE_ONLY`.

All accepted project-memory requests remain proposal-only. A returned receipt can
state that operator approval is required, but it must not mark CANON as written
or expose `proposed_value` content. Canonical promotion still requires explicit
operator approval through the normal Memory Gateway proposal flow.

Public receipts are intentionally narrow: status, namespace, project id,
classification, decision, gateway contract metadata, proposal status, event ref,
idempotency classification, and proposal-only flags. Raw private values, local
paths, SQLite details, secrets, provider payloads, and record bodies are not
included.
