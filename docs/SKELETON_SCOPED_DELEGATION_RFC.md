# RFC: Scoped Delegation and Capability-Based Authorization for Skeleton

Status: REVIEW
Owner: Skeleton
Human authority: operator
Scope: Skeleton execution authorization, integrity, audit, risk, recovery, and migration

## 1. Problem statement

Skeleton currently applies several independent controls at per-request granularity. In practice, real authorization boundaries, request-integrity checks, idempotency/replay protection, audit persistence, execution lanes, and policy decisions can all surface as one generic blocked outcome. This creates two different failures that look alike:

1. legitimate security boundaries correctly deny unsafe or unapproved work;
2. control-path defects or redundant per-call gates deny work that is already inside an explicitly delegated task.

Observed examples include:

- a minimal `/bin/true` request succeeding while a longer read-only request fails with `request signature mismatch`;
- apparently fresh idempotency keys being rejected as already used for a different payload;
- routine mutation inside an already delegated task still requiring a fresh per-call `operator_approval_ref`.

The target is not weaker security. The target is fewer ambiguous gates, explicit delegation boundaries, stronger separation of concerns, and less repeated approval inside a scope the operator has already granted.

## 2. Design goals

The new model MUST:

- preserve operator authority;
- prevent agents from self-granting capabilities;
- replace repeated per-call approval with bounded delegation where safe;
- make capability scope explicit and resource-scoped;
- keep destructive, external, credential, production, network/security, firmware, and policy-authority changes behind fresh human approval;
- separate authorization from integrity, idempotency, audit, execution, and verification;
- account for cumulative/composed risk, not only per-action risk;
- prevent concurrent write sessions from interfering on the same resource scope;
- require verified rollback before treating privileged mutations as automatically reversible;
- support partial revocation and emergency policy invalidation;
- fail closed when historical or runtime evidence is insufficient;
- migrate through shadow evaluation and tier-by-tier promotion, never a big-bang switch.

## 3. Non-goals

This RFC does NOT:

- create a globally trusted-agent mode;
- let an LLM infer and grant its own authority from natural-language intent;
- weaken existing destructive-action, credential, network-exposure, firmware, factory-reset, production-promotion, or external-send boundaries;
- allow a delegation session to register or modify privileged operations for itself;
- treat a successful command exit code as proof of real-world success;
- automatically promote this RFC to CANON or production authority.

## 4. Core principle

Skeleton should move from:

`each argv -> approval -> signature -> nonce -> idempotency -> lane -> policy -> executor -> repeat`

Toward:

`human intent -> proposed capability set -> human grant -> delegation session -> risk/composition engine -> integrity envelope -> executor -> independent verification -> audit`

The agent MAY propose a capability set based on the operator's goal, but MUST NOT activate, widen, or grant that set. The operator grants the exact capability set and resource scopes.

## 5. Trust and authority model

### 5.1 Human grant is authoritative

Natural-language intent is descriptive context only. It is never sufficient authorization by itself.

Example intent:

`Finish Qwen local inference to a working state.`

The system may propose:

- `read:home-edge-state` scoped to `home-edge-01`;
- `write:git-branch` scoped to one exact review branch;
- `restart:service` scoped to `skeleton-local-inference.service`;
- `invoke:registered-operation` scoped to pinned operation versions.

The delegation session becomes active only after the operator explicitly grants the enumerated capabilities and resource scopes.

### 5.2 Agent authority can narrow but never widen

At runtime the agent MAY voluntarily reduce its authority, release leases, or revoke a capability from its own session. It MUST NOT add new capabilities, widen scopes, increase risk budget, change pinned operation versions, or change policy authority.

### 5.3 No global model trust

Authority attaches to a session, capability, resource scope, version, and budget. It does not attach to a model identity as a general trust level.

## 6. Delegation session schema

A normative implementation should expose an immutable grant snapshot plus mutable runtime state.

```yaml
session_id: string
goal_text: string
status: PENDING | ACTIVE | SUSPENDED | PENDING_RECOVERY_DECISION | COMPLETED | EXPIRED | REVOKED | FAILED
created_at: timestamp
expires_at: timestamp
granted_by: operator_ref
granted_at: timestamp

capabilities:
  - capability_id: string
    verb: string
    resource_type: string
    resource_scope: string
    status: PENDING | ACTIVE | REVOKED | EXPIRED
    granted_at: timestamp
    revoked_at: timestamp|null
    revoked_by: operator_ref|null
    revocation_reason: string|null

registered_operations:
  - operation_id: string
    operation_version: string
    operation_digest: sha256
    registered_at: timestamp

risk_budget:
  granted_by: operator_ref
  granted_at: timestamp
  max_mutation_weight: number
  max_resources_touched: integer
  max_diff_size: integer
  max_service_restarts: integer
  max_privilege_transitions: integer
  retry_budget: integer
  runtime_budget_seconds: integer

rollback_policy:
  rollback_verified_required: true

write_leases: []

grant_snapshot:
  capability_set_hash: sha256
  operation_set_hash: sha256
  risk_budget_hash: sha256
  policy_version: string
  session_schema_version: string
```

The grant snapshot is immutable for normal operation. Any authority change requires a new human action, except the emergency security override defined later, which may only narrow or invalidate authority.

## 7. Capability model

### 7.1 Resource-scoped capabilities

Capabilities MUST include an explicit resource scope. Unscoped verbs are invalid for delegated execution.

Examples:

```text
read:state:home-edge-01
write:git-branch:alanua/Skeleton:review/local-llm-shadow-eval-20260905
restart:service:home-edge-01:skeleton-local-inference.service
invoke:operation:de_pc_status_v1@1.0.0#<digest>
```

### 7.2 Capability activation

A capability begins in `PENDING`, becomes `ACTIVE` only after human grant, and may later become `REVOKED` or `EXPIRED`.

### 7.3 Capability membership replaces repeated approval

If an action is fully contained within an `ACTIVE` capability, inside the granted resource scope and budgets, and does not cross a fresh risk boundary, Skeleton SHOULD NOT request another per-call human approval.

### 7.4 Privileged operation registration is out-of-band

A delegation session MUST NOT create, modify, or approve a privileged operation that it can invoke.

For privileged automatic invocation, all of the following MUST hold:

- operation registration predates the session;
- exact `operation_version` is pinned in the grant;
- exact `operation_digest` matches the granted snapshot;
- rollback requirements for the operation are satisfied;
- the operation remains inside the resource and risk scope.

Normative invariant:

```text
operation.registered_at < session.created_at
```

Any operation version or digest change requires a new human grant.

## 8. Risk tiers

Skeleton should classify actions by real risk boundary, not merely by which executor lane they currently use.

### Tier R0: read-only

May execute without repeated approval inside an active capability. Read-only MUST be structurally enforced by the execution environment or permissions, not just by a policy label.

### Tier R1: reversible routine mutation

May execute without repeated approval inside an active capability when resource scope, cumulative-risk budget, retries, leases, and verification conditions remain satisfied.

### Tier R2: privileged reversible mutation

May execute automatically only if it invokes a pre-registered, version-pinned, digest-pinned operation with verified rollback and explicit capability grant.

### Tier R3: fresh-human-approval required

Includes at minimum:

- destructive or irreversible changes;
- secrets, credentials, authentication material;
- external send/publish or externally visible side effects;
- production promotion;
- network exposure, firewall, router, modem, DHCP, DNS, VLAN, or security-boundary changes;
- firmware, factory reset, destructive device actions;
- changes to authorization policy, approval policy, delegation policy, or operation-registration authority;
- widening capability scope or risk budget.

## 9. Verified rollback

`reversible` is not a descriptive label. It is an evidence-backed property.

A privileged operation is eligible for the reversible tier only if it carries at least:

```yaml
rollback_defined: true
rollback_tested: true
rollback_verified: true
rollback_verified_at: timestamp
rollback_evidence: receipt_ref
```

If `rollback_verified != true`, fail closed and classify the operation into the fresh-approval tier.

Rollback verification MUST test the actual end-to-end restoration path, not merely existence of a rollback command.

## 10. Cumulative risk ledger

Per-action safety is insufficient because individually safe operations can compose into an unsafe total effect.

Each active session MUST maintain an externally persistent cumulative-risk ledger covering at least:

```yaml
distinct_resources_touched: integer
files_modified: integer
total_diff_size: integer
services_restarted: integer
configuration_domains_changed: integer
privilege_transitions: integer
network_state_changes: integer
rollback_dependencies: integer
mutation_weight: number
```

The exact weighting model is policy-versioned and reviewable.

The `cumulative_risk_budget` is granted by the human operator. System defaults MAY be used as UI recommendations or hard maximums, but MUST NOT silently become the effective authorization grant.

Increasing any risk limit requires fresh human approval. Skeleton may automatically reduce limits.

When a cumulative limit is crossed, the session returns `CUMULATIVE_RISK_EXCEEDED` and requires a new human decision.

## 11. Composition-aware checks

The policy engine MUST evaluate capability composition, not only each capability independently.

Known dangerous combinations include:

- `write:review-branch` + unrestricted `run:test-suite` becoming arbitrary code execution;
- configuration write + service restart changing effective authority;
- multiple reversible mutations whose rollback dependencies become mutually incompatible;
- one session creating state another active session assumes is stable.

High-risk capability combinations SHOULD require either a pre-reviewed composition rule, a constrained execution manifest, or fresh human approval.

## 12. Resource leases and concurrency

Mutable resources use leases.

```yaml
resource_scope: string
session_id: string
lease_mode: read | write
acquired_at: timestamp
expires_at: timestamp
generation: integer
status: ACTIVE | RELEASED | PENDING_RECOVERY_DECISION
```

Rules:

- read/read overlap may be allowed;
- write/read or write/write overlap on the same protected scope is blocked unless a specific reconciliation rule exists;
- `lease.session_id` MUST equal the owning delegation session;
- `lease.expires_at <= session.expires_at`;
- lease renewal cannot outlive or independently extend the session;
- `COMPLETED`, `EXPIRED`, `REVOKED`, or `FAILED` session termination releases ordinary leases atomically;
- a scope in `PENDING_RECOVERY_DECISION` remains locked and cannot be claimed by another write session.

## 13. Partial revocation and recovery

Capability-level revocation is supported independently of full session revocation.

On revocation Skeleton MUST:

1. prevent any new action using that capability;
2. cancel or block queued actions that have not yet been applied;
3. release related leases only when safe;
4. preserve audit evidence;
5. evaluate whether already-applied state needs rollback or operator recovery.

If an already-applied mutation cannot be safely rolled back automatically, the affected resource enters:

`PENDING_RECOVERY_DECISION`

During this state:

- the resource remains write-locked;
- no new session may claim the same mutable scope;
- recovery policy may propose options but does not silently choose a higher-risk action;
- the operator resolves the state explicitly unless a pre-approved recovery operation is already applicable.

This prevents a revoked-but-still-mutated resource from becoming apparently unowned and immediately writable by another session.

## 14. Policy emergency override

Grant snapshots pin `policy_version` to prevent silent authority drift. However, a critical vulnerability in the policy engine must be able to invalidate unsafe active grants.

Skeleton therefore defines `policy_emergency_override` as a separate human-controlled security mechanism.

It MAY:

- force revalidation of active sessions against a newer policy version;
- suspend sessions pending revalidation;
- expire sessions that are no longer safe;
- revoke specific capabilities;
- force resource scopes into `PENDING_RECOVERY_DECISION` where required.

It MUST NOT silently widen authority.

Every emergency override MUST be:

- explicitly human-authorized;
- separately audited;
- bound to the security policy version or advisory that triggered it;
- distinguishable from routine policy updates.

Routine policy updates do not mutate active grant snapshots.

## 15. Request Integrity Envelope

Signature, nonce/replay protection, and idempotency remain separate mechanisms internally but are treated as one logical integrity layer with one diagnostic boundary.

The integrity envelope answers only:

- is this payload authentic;
- is it fresh/not a replay;
- is this the intended operation identity;
- is this a legitimate retry or a conflicting reuse.

It does NOT decide authorization.

An integrity failure MUST NOT be reported as a policy denial.

## 16. Denial and failure taxonomy

Skeleton SHOULD adopt a machine-readable top-level reason taxonomy early, before the delegation model changes production authority:

```text
POLICY_DENIED
APPROVAL_REQUIRED
CAPABILITY_MISSING
INTEGRITY_FAILURE
IDEMPOTENCY_CONFLICT
EXECUTION_FAILURE
VERIFICATION_FAILURE
SAFETY_PLATFORM_BLOCK
RESOURCE_LEASE_CONFLICT
CUMULATIVE_RISK_EXCEEDED
ROLLBACK_NOT_VERIFIED
INSUFFICIENT_EVIDENCE
SESSION_EXPIRED
SESSION_REVOKED
PENDING_RECOVERY_DECISION
```

Each result should also carry a specific internal reason code and audit reference.

## 17. Execution and verification state machine

Authorization and successful execution are distinct.

Skeleton should record at minimum:

```text
REQUESTED
AUTHORIZED
SENT
ACCEPTED
APPLIED
PHYSICALLY_VERIFIED
```

Failure or interruption states include:

```text
DENIED
FAILED
VERIFICATION_FAILED
ROLLED_BACK
PENDING_RECOVERY_DECISION
```

Exit code zero, HTTP 2xx, service active, or command accepted is not sufficient proof of `PHYSICALLY_VERIFIED`.

## 18. Audit requirements

Audit state MUST live outside processes that delegated capabilities are allowed to restart. Restarting a service must not reset mutation counters, retry budgets, leases, or cumulative-risk state.

Receipts should include enough structured evidence to reconstruct:

- session and capability used;
- resource scope;
- operation version/digest where relevant;
- policy version;
- integrity result;
- risk classification;
- cumulative-risk delta;
- execution transition;
- verification result;
- lease lifecycle;
- rollback or recovery outcome.

## 19. Counterfactual replay

Historical audit receipts may be replayed through the new policy engine to estimate disagreements without executing actions.

Replay evidence is valid only after an explicit evidence-sufficiency gate.

For each historical receipt, the replay engine MUST verify that every field required by the new decision model is present or derivable from independently trustworthy evidence. Missing required fields MUST produce:

`INSUFFICIENT_EVIDENCE`

The replay engine MUST NOT silently substitute old classifications, infer missing resource scope from a prior policy label, or treat absent data as zero risk.

### 19.1 Coverage threshold

A review-approved minimum `historical_replay_coverage_threshold` MUST be defined before replay results can support promotion.

Below the threshold:

- replay statistics are informational only;
- allow/deny agreement rates MUST NOT be used as promotion evidence;
- missing-evidence coverage is reported separately and prominently.

The exact threshold is a governance/evaluation parameter and requires explicit review before the replay campaign begins.

## 20. Adversarial scenarios and required mitigations

### 20.1 Goal-scope laundering

Threat: broad intent is used to justify wider capabilities than the human meant.

Mitigation: the agent may propose but never grant or widen capabilities; human grants exact capability/resource scope.

### 20.2 Restart-based budget laundering

Threat: restarting an owned service resets in-memory audit, counters, or budgets.

Mitigation: all delegation, budget, lease, and audit state persists outside restartable target processes.

### 20.3 Composed-effect escalation

Threat: individually reversible operations compose into an irreversible or higher-risk system state.

Mitigation: cumulative-risk ledger, composition rules, transactional grouping where possible, and fresh approval at cumulative thresholds.

### 20.4 Test-suite arbitrary execution

Threat: write permission plus test execution becomes arbitrary code execution.

Mitigation: signed/reviewed test manifests, sandboxing, capability-composition checks, or separate approval for write+execute combinations.

### 20.5 Concurrent-session interference

Threat: two valid sessions interleave writes that jointly cross a risk boundary.

Mitigation: resource-scoped leases, conflict detection, and reconciliation before overlapping write scopes proceed.

## 21. Migration plan

### Phase 0: repair current control-path defects

Fix signature mismatch, idempotency collision/normalization, and similar defects under the current security model. Do not attribute these defects to legitimate policy caution.

### Phase 1: denial taxonomy

Ship machine-readable failure classes while preserving current allow/deny behavior.

### Phase 2: delegation model in shadow mode

For each real request, compute the new model's hypothetical decision in parallel. Existing gates continue to control execution.

Record every divergence between old and new decisions.

### Phase 3: counterfactual replay

Replay historical receipts only after evidence-sufficiency validation. Reject insufficient records rather than filling gaps with assumptions.

### Phase 4: human review of divergences

Review cases where the new model would allow actions the old model denied, and cases where the new model would deny actions the old model allowed.

### Phase 5: promote R0/read-only

Only after shadow/replay evidence meets predeclared acceptance criteria and receives explicit human sign-off.

### Phase 6: promote R1/routine mutation

Run its own shadow period and promotion review. Do not infer readiness from R0 results.

### Phase 7: promote R2/privileged reversible operations

Require pinned operations, verified rollback, cumulative-risk controls, leases, and a separate promotion decision.

### Phase 8: retain fresh approval for R3

R3 remains human-gated unless a future separately reviewed RFC explicitly changes a specific boundary.

## 22. Promotion criteria

Before each tier is promoted, the review MUST define and satisfy:

- minimum shadow sample size;
- minimum evidence coverage;
- maximum false-allow rate;
- maximum unexplained divergence rate;
- rollback verification requirements;
- concurrency/lease tests;
- adversarial scenario tests;
- operator sign-off.

No risk tier inherits promotion merely because a lower tier passed.

## 23. Required tests

At minimum:

- agent cannot widen a granted resource scope;
- expired session cannot retain or renew a lease;
- revoked capability cannot enqueue new work;
- partial revocation with applied state enters `PENDING_RECOVERY_DECISION` when rollback is unsafe;
- another session cannot claim a scope pending recovery;
- operation version/digest drift invalidates invocation;
- operation registered after session start is not auto-invokable;
- unverified rollback forces fresh approval;
- cumulative risk threshold stops a chain of individually routine mutations;
- write+test composition cannot become arbitrary execution without the intended extra control;
- target-service restart does not reset session budget or audit state;
- emergency policy override can suspend/revalidate/expire sessions but cannot widen them;
- integrity failure cannot surface as `POLICY_DENIED`;
- replay with missing required fields returns `INSUFFICIENT_EVIDENCE`;
- replay below coverage threshold cannot satisfy promotion criteria;
- same-scope concurrent write sessions conflict deterministically;
- verification state cannot jump from `AUTHORIZED` directly to `PHYSICALLY_VERIFIED` without required evidence.

## 24. Security invariants

1. No agent self-grants authority.
2. Authority is explicit, resource-scoped, version-pinned, budgeted, and time-bounded.
3. Active authority can be narrowed or revoked without granting anything new.
4. Privileged operation registration is outside delegated execution authority.
5. `rollback_verified=true` is required before privileged automatic reversible execution.
6. Cumulative semantic risk can stop execution even when every individual action is locally permitted.
7. Mutable scope ownership is explicit and lease-protected.
8. Revoked but unresolved state cannot become implicitly free for another writer.
9. Routine policy changes cannot silently alter active grants.
10. Emergency policy override may invalidate unsafe grants but never widen them.
11. Integrity, authorization, execution, and verification are separately observable.
12. Missing evidence fails closed.
13. Migration proceeds by risk tier with explicit promotion decisions.

## 25. Open implementation questions

The architecture is considered closed enough for RFC review, but implementation still requires concrete choices for:

- capability/resource pattern syntax;
- persistent session/lease store and crash recovery;
- mutation-weight calculation;
- exact replay coverage threshold and promotion statistics;
- how read-only structural enforcement maps onto each executor/backend;
- signed test-manifest format;
- policy-emergency-override operator UX;
- schema/version migration for existing registered operations.

These are implementation and calibration questions, not changes to the authorization model defined above.

## 26. Relationship to current Skeleton canon

This RFC is a REVIEW proposal layered on top of the current contracts, not a replacement that is already in force. In particular:

- `docs/HOME_EDGE_EXECUTOR.md` remains authoritative for current execution lanes, per-call approval, HMAC signing, nonce, idempotency, and receipts until a separately approved implementation changes them;
- `docs/SKELETON_GOVERNANCE_CONTRACT_V1.md` remains authoritative that current governance task templates do not themselves grant runtime authority;
- `docs/ACTION_GATE.md` remains the current bounded action-gate contract where applicable;
- existing destructive, credential, network/security, firmware, deployment, merge, and canon-promotion approval requirements remain unchanged during shadow evaluation.

The intended operator UX may present a proposed capability bundle and its exact resource scopes as one reviewable grant action. This reduces approval friction without letting the agent infer or self-issue authority.

## 27. Rollout safety rule

No part of this RFC changes production authority merely by being merged as documentation. Any implementation must first run under existing Skeleton approvals/audit, then shadow evaluation, then explicit tier-specific human promotion.
