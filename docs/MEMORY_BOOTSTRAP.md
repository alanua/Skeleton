# Memory Bootstrap

`core.memory_bootstrap` composes the local-private memory stack for Runner use. It requires an exact `project_id` and `dataset_id`, a configured private root, and an initialized `PrivateMemoryStack`.

The bootstrap creates a private-mode `MemoryGateway` backed by `PrivateMemoryGatewayStorage`. Canonical revision is read from the gateway status, not from caller input. If no exact keys are supplied, the bootstrap reads a bounded exact list for the declared dataset.

Projection selection is deterministic:

- Cognee is used first only when injected, `READY`, and bound to the current canonical revision.
- MemPalace is a fresh fallback from the stack index path.
- Graphify is included only when its indexed revision equals the canonical revision.

Runner handoff writes the full context to an owned `0600` temp file outside the repo. Public reports contain only bounded receipts and must not include raw values, paths, canonical refs, or provenance.
