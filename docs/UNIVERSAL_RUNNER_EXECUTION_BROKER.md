# Universal Runner Execution Broker

## Goal

Runner is a general execution plane. New tasks do not require a new task-specific handler. A validated local `TaskEnvelope` selects one generic executor class and supplies the concrete operation.

## Flow

1. The operator request is converted to a private `TaskEnvelope`.
2. The envelope is validated for schema, risk, approval, privacy, timeout and idempotency.
3. The broker resolves one generic executor class.
4. Steps execute in order and stop on the first failure.
5. Generic assertions verify the result.
6. Full evidence remains local. GitHub receives only an aggregate receipt.

## Generic executor classes

- `local.process`: argv-based local process execution.
- `remote.ssh`: argv execution on a registered target with strict host-key verification.
- `network.http`: bounded HTTP requests.
- `python.entrypoint`: calls to pre-registered Python callables.
- `filesystem`: bounded operations below registered roots.
- `repository`: git argv below registered repository roots.
- `composite`: ordered composition of the preceding classes.

Devices, services and individual actions are data in the envelope or local target registry. They are not new Runner handlers.

## Approval

- `green`: read-only or reversible operations allowed by policy.
- `yellow`: requires exact operator approval.
- `red`: requires operator approval plus second-stage approval.

Approval is based on side effects and target scope, not task names.

## Privacy

The complete envelope, request bodies, local paths, target configuration and complete evidence packet remain in local private storage. Public output is restricted to hashes, counts, executor class, risk class and final state.

## Memory boundary

Local private-memory search has no semantic word denylist. Public and synthetic adapters retain their public-safe validation. Privacy filtering belongs at export boundaries, not inside local storage or local retrieval.

## Migration

Existing maintenance handlers remain compatibility shims while their operations are expressed as TaskEnvelopes. No new task-specific handlers should be added. Codex and other models are optional workers, never the Runner control plane.
