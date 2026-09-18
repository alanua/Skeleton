# Threat model

Skeleton operates at a higher-risk boundary than a normal code-generation library because an AI-assisted workflow may eventually affect source repositories, CI, services, edge machines, or physical devices.

This document describes the public threat model. It does not disclose private infrastructure, credentials, or device topology.

## Security goals

Skeleton is designed to preserve the following properties:

1. **Human authority** — privileged or security-sensitive actions remain subject to explicit approval boundaries.
2. **Bounded execution** — workers use registered executors, identities, lanes, timeouts, and operation definitions rather than arbitrary production shell access.
3. **Least privilege** — read-only and local-only paths are preferred when sufficient.
4. **Source/runtime separation** — public source does not become a storage location for private runtime state or secrets.
5. **Auditability** — important mutations produce evidence that can be tied to the requested action and resulting state.
6. **Independent verification** — command success alone is not treated as proof that the intended real-world state was achieved.
7. **Recoverability** — risky changes preserve rollback information and prior failure history.
8. **Fail-closed dependencies** — broken or missing authoritative dependencies should block rather than silently fall back to stale behavior.

## Trust boundaries

```text
Human maintainer
      |
      v
Assistant / coding agent
      |
      v
Project + policy routing
      |
      v
Approval / ActionGate
      |
      v
Registered Runner / executor
      |
      +------> source repository / CI
      |
      +------> service / Home Edge node
                    |
                    v
             observable post-condition
                    |
                    v
              audit / recovery state
```

Each transition is a trust boundary. The agent is not assumed to be a privileged principal merely because it can generate a command or patch.

## Primary threat classes

### 1. Prompt or task injection

An issue, document, webpage, generated patch, or external source may contain instructions that conflict with maintainer intent.

Mitigations include:

- declared project/task scope;
- explicit operation/risk boundaries;
- separation of untrusted content from authority;
- approval gates for privileged actions.

### 2. Over-broad execution

A correct high-level goal can still produce an unsafe command, excessive filesystem access, or an unnecessarily privileged deployment path.

Mitigations include:

- registered execution lanes;
- exact or bounded operations;
- explicit run-as identity;
- timeouts and idempotency;
- preconditions and post-condition verification.

### 3. Cross-repository confusion

A worker may modify the wrong repository, stale branch, compatibility copy, or non-canonical source.

Mitigations include:

- canonical project manifests;
- repository/project routing;
- exact source revision checks;
- explicit cross-repository contracts;
- fail-closed behavior when the authoritative dependency is unavailable.

### 4. Supply-chain and dependency compromise

Dependencies, actions, packages, or generated artifacts may introduce malicious or unexpected behavior.

Mitigations include:

- pinned revisions where operationally important;
- source/artifact provenance checks;
- CI validation;
- public dependency boundaries;
- review before runtime activation.

### 5. Secret or private-state disclosure

LLM context, logs, issue text, patches, or public repositories can accidentally receive credentials or private runtime information.

Mitigations include:

- separate secret stores;
- public/private routing rules;
- repository scans;
- no plaintext secret persistence in canonical public source;
- sanitised public receipts.

### 6. False-positive execution success

A command may return zero even when the service, application, display, or device did not reach the intended state.

Mitigations include:

- distinction between sent, accepted, applied, and independently verified;
- service/API/state checks;
- observable post-conditions;
- rollback when verification fails.

### 7. Persistence and rollback failure

An apparently successful change may leave stale state, incomplete continuity records, or no safe way back.

Mitigations include:

- backup-before-mutation patterns;
- durable state/history;
- reusable registered operations;
- explicit rollback instructions;
- failure-history-aware retry rules.

## Why Codex Security is relevant

Skeleton exposes exactly the kind of boundary where security review has leverage: executor contracts, approval gates, GitHub workflows, cross-repository dependencies, Android/Home Edge integrations, runtime-maintenance paths, and rollback logic.

Security findings can be converted into public regression tests and reusable hardening patterns instead of remaining one-off production fixes.

## Out of scope for the public repository

The threat model does not publish:

- live credentials or API tokens;
- private keys;
- private addresses/topology;
- personal user data;
- private device registries;
- production runtime databases.

Those remain private runtime concerns even when the public control contract is open source.
