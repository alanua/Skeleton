# TaskEnvelope idempotency

The local TaskEnvelope runtime can persist a private receipt by `idempotency_key`.

Execution rules:

- the first execution runs the envelope and stores its public receipt in a private directory;
- a repeated execution with the same key and canonical envelope hash returns the stored receipt without re-running side effects;
- reusing the same key for a different canonical envelope fails closed;
- a per-key file lock serializes concurrent attempts;
- full execution evidence remains in the configured private evidence directory.

The idempotency directory and stored receipt files use owner-only permissions.
