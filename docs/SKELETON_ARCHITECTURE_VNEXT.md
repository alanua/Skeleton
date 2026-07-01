# Skeleton Architecture VNext

Status: ACTIVE_ARCHITECTURE / OPERATOR_APPROVED / HUMAN_GATED

This document is the current target architecture for Skeleton VNext. This
document is repository canon only while present on reviewed GitHub `main`.
This document is repository canon only while present on reviewed GitHub `main`.
Until then, GitHub `main` remains public control/code/policy canon,
`BOOT_MANIFEST.yaml` remains the entrypoint, and this task must not modify
`BOOT_MANIFEST.yaml`.

Canonical private SQLite remains the private source of truth for approved private
facts, decisions, overrides, provenance, and task history. Public GitHub records
carry public-safe control, code, policy, review, and supersession material.

## Identity And Boundary

Skeleton is the human-controlled, model-neutral construction, governance, and
execution-control framework.

Skeleton is not Jeeves, not a product assistant runtime, not a secrets store, and
not an autonomous organization. Jeeves remains a separate future
assistant/product/runtime that may reuse only reviewed Skeleton patterns and
interfaces.

## Main Architecture Diagram

```text
Operator
  -> Governance Kernel
  -> Loop Controller
  -> Runner Execution Broker
       -> worker adapters: Codex / OpenHands-style makers
       -> reviewer adapters: Gemini / Claude / Kimi-style auditors
       -> Hermes bounded worker/interface
       -> Home Edge Node: home-edge-01
  -> Evidence and Delivery Plane
  -> GitHub review and human merge gate
```

## Governance Kernel

The Governance Kernel is the single authority and policy layer. It contains boot
and authority precedence, classification and privacy routing, executable rule
registry, capability and actor-role checks, Approval Object validation,
protected-resource detection, public-safe reason tokens, and append-only audit
evidence.

Authority resolves in this order:

1. Current explicit operator instruction for the task.
2. Reviewed GitHub `main` control files.
3. Validated project/task contracts.
4. Approved private canonical state when relevant.
5. Derived indexes and worker evidence.
6. Model inference and chat memory.

Lower layers can provide context or evidence, but they cannot silently override
higher layers.

## Loop Controller

Loop Engineering is a central VNext component:

```text
trigger -> classify -> isolate -> execute -> validate -> persist state -> decide -> stop/retry/escalate
```

The canonical loop state model is:

- `CREATED`
- `READY`
- `RUNNING`
- `CHECKPOINTED`
- `NEEDS_OPERATOR`
- `HUMAN_REVIEW`
- `BLOCKED`
- `CANCELLED`
- `DONE`

Required controls are short clean-context iterations, external process memory,
`max_iterations`, timeout, bounded retries and budget, lease/heartbeat,
checkpoint/resume/cancel, maker/checker separation where risk justifies it, and
machine-verifiable success criteria where possible.

Passing tests never imply automatic merge. Repeated error, protected scope,
privacy boundary, or unsafe fallback must stop and escalate.

## Canonical TaskEnvelope

TaskEnvelope is the canonical execution request. GitHub Issues are the durable
carrier and queue view, not the only state database and not the approval object.

Required fields and concepts:

- `task_id` and idempotency key.
- Project, repository, branch/worktree.
- Action separate from `executor_type`.
- Goal and allowed scope.
- `allowed_files` and `forbidden_actions`.
- Validation and expected output.
- Privacy boundary.
- Risk level.
- Timeout/retry/checkpoint policy.
- Actor role and selected adapter.
- Approval evidence references.
- Expected evidence packet.
- Publish capability result.

## Approval Object

Approval Object is action-specific approval bound to the actor/operator, exact
action, target repository/node/device, exact target state, plan hash or reviewed
head SHA, allowed side effect, expiry, required evidence, and forbidden
shortcuts.

Generic chat agreement, task completion, model output, or labels must not be
inferred as permission for merge, deploy, runtime mutation, secrets, canon
promotion, or destructive actions.

## Execution Broker And Worker Roles

Runner is the controlled execution broker. Workers and models are adapters
selected by `actor_role`, capabilities, risk, privacy, and available credentials.
Model names are not authority identities.

Minimum roles:

- Operator: approval authority and final human gate.
- Planner/reviewer: plans, scopes, critiques, and reviews.
- Execution broker: dispatches, validates, publishes, and reports.
- Bounded maker/executor: changes only assigned scope.
- Checker/auditor: reviews evidence, risks, and compliance.
- Local/physical node executor: performs allowlisted local physical tasks.
- Status/notification projection: displays state without becoming canon.

Role boundaries:

- ChatGPT plans, scopes, and reviews.
- Runner owns controlled dispatch, worktrees, validation, publication mechanics,
  and result reporting.
- Codex/OpenHands-style workers are bounded makers, never authority.
- Gemini/Claude/Kimi-style workers are temporary reviewers/auditors unless
  separately assigned a tested bounded role.
- Hermes is a bounded worker/interface and uses Memory Gateway, not direct
  canonical writes.
- Multi-model output is evidence, not majority-vote truth.

## Home Edge Node

Current node id: `home-edge-01`.

Role: universal controlled local execution node.

Controller path:

```text
Skeleton Governance/Loop -> Hetzner Runner -> Tailscale transport -> home-edge-01
```

Status: architecture-approved. Implementation from #1253/#1254 remains unmerged
until separately reviewed and merged. #1256 remains planned controlled-mutation
capability, not proof of live execution.

Purpose:

- Perform local tasks that cloud Runner cannot safely perform directly.
- Home network and router diagnostics/configuration.
- USB, modem, and serial work.
- ESP/WLED build, OTA, and serial flashing.
- Media and projector workloads.
- Docker/local services.
- Future Home Assistant and smart-home support.
- Local/private or latency-sensitive work.
- Avoid using the operator as a routine terminal typist.

Home Edge boundaries:

- Home Edge is execution plane, not control plane and not canon.
- Public unrestricted shell or agent API is prohibited.
- Transport must be allowlisted and audited once activated.
- Node must use a versioned NodeProfile/capability manifest once activated.
- Every mutation must use an ActionPlan with plan/dry_run/execute/verify/rollback
  phases once activated.
- Exact device identity, free space, power, network, and recovery path must be
  verified before mutation.
- Primary connectivity and Tailscale recovery must be preserved.
- Atomic writes, backups, execution ids, and idempotency must be used.
- Secrets and private payloads must remain local/private and must never enter
  GitHub reports.
- Public reports contain only status, hashes, bounded metadata, evidence
  references, and stable reason tokens.
- Actions use green/yellow/red classes.
- Firmware build/validation is green.
- Verified recoverable ESP/WLED OTA or serial flashing is yellow.
- Modem/router/bootloader firmware, disk/boot/primary-gateway, or
  recovery-threatening action is red.
- Future additional edge nodes must use the same NodeProfile, transport,
  ActionPlan, and Evidence Packet contracts.

## Evidence And Delivery Plane

Worker Evidence Packet includes at least claim/result, authority sources read,
files/resources read and touched, commands/tools invoked in public-safe form,
validation and tests, target/head/plan hashes, risks and privacy result,
rollback/postflight status when applicable, publish result, and next valid
gate/action.

Delivery invariant: before expensive execution, Runner must preflight workspace
creation, writable Git metadata, authentication, push capability, draft
PR/publication capability, and expected destination. If `can_publish=false`, do
not run the full maker task unless the operator explicitly approves a
local-only/checkpoint result.

## Memory And State Plane

Skeleton has two distinct authority classes even if implemented in one SQLite
service initially:

- Operational task/run/event state for tasks, runs, events, checkpoints, leases,
  and delivery.
- Approved private canonical facts/decisions/overrides/provenance for private
  canon.

Memory Gateway is the only normal entrance. Hermes/Skeleton may exact-read and
create PatchProposal. Canonical writes require schema validation, identity,
dedupe, idempotency, reconciliation, approval, append-only audit, and readback
verification.

Graphify and MemPalace are separate optional derived/rebuildable indexes.
Graphify is relationship/dependency orientation. MemPalace is semantic
retrieval. Neither can write canon or override SQLite/GitHub/operator authority.
Private/public namespace isolation and deletion/backup/freshness contracts are
mandatory.

## Memory And Projection Diagram

```text
GitHub public canon + private SQLite canon
  -> Memory Gateway
       -> Graphify derived relationship/dependency index
       -> MemPalace derived semantic retrieval index
  -> read-only projections: Telegram / Control Board / NotebookLM / future UI
```

## Interface Projections

Telegram, Control Board/dashboard, NotebookLM/sourcepacks, and future UI are
projections over authoritative state. They may display, request bounded actions,
or prepare proposals, but must not become canon or bypass the TaskEnvelope, Loop
Controller, Approval Object, Evidence Packet, or GitHub review boundary.

## Architecture And Implementation Status Matrix

| Component | architecture_status | implementation_status |
| --- | --- | --- |
| `governance_kernel` | Target architecture approved; authority ordering, privacy routing, protected-resource detection, and audit evidence must govern VNext work. | Partially implemented on reviewed GitHub `main` through boot/control files, operator rules, policy docs, tests, and review gates; no autonomous runtime authority is live. |
| `loop_controller` | Target architecture approved; loop state, bounded retries, checkpoints, leases, and stop/escalate behavior must define future execution. | Partially implemented by existing Runner issue/worktree/test/PR flow; the full canonical Loop Controller is not live as a separate activated runtime. |
| `task_envelope` | Target architecture approved; TaskEnvelope must be the canonical execution request. | Partially implemented through GitHub issue task contracts and Runner scope fields; no standalone canonical TaskEnvelope service is live. |
| `approval_object` | Target architecture approved; approvals must be action-specific, state-bound, expiring, evidenced, and forbidden from generic inference. | Partially implemented through reviewed approval gates and action-gate policy; no general Approval Object runtime may be treated as live. |
| `evidence_packet` | Target architecture approved; workers must produce public-safe evidence packets for claims, touched resources, validation, risks, and next gates. | Partially implemented through Runner DONE/BLOCKED reports and tests; the full canonical Evidence Packet schema is not activated as a live runtime contract. |
| `delivery_preflight` | Target architecture approved; Runner must verify workspace, writable Git metadata, auth, push, draft PR capability, and destination before expensive execution. | Planned/non-live as the VNext `can_publish` preflight; current host-only smoke or task success must not be called the activated delivery preflight. |
| `private_sqlite_memory_gateway` | Target architecture approved; Memory Gateway must be the normal entrance and canonical writes must require validation, approval, audit, and readback. | Reviewed `main` contains public-safe memory gateway/canonical SQLite components and tests; private canonical activation/import is not live unless separately activated with operator-approved local runtime evidence. |
| `graphify` | Target architecture approved as a private derived relationship/dependency index that must not write canon or outrank SQLite/GitHub/operator authority. | Reviewed `main` contains bounded public-safe Graphify adapter evidence; Graphify runtime, ingestion, Gateway activation, and private graph data access are not live. |
| `mempalace` | Target architecture approved as a private derived semantic retrieval index that must remain rebuildable and non-authoritative. | Reviewed `main` contains bounded synthetic MemPalace pilot evidence; MemPalace runtime, Gateway activation, and private semantic index access are not live. |
| `home_edge_transport` | Target architecture approved; Home Edge transport must be allowlisted, audited, node-profiled, and recovery-preserving once activated. | #1253/#1254 remain unmerged evidence; no Home Edge transport is live from this document or this PR. |
| `home_edge_mutations` | Target architecture approved; mutations must use ActionPlan plan/dry_run/execute/verify/rollback and exact risk approval once activated. | #1256 remains planned controlled-mutation capability; no Home Edge mutation execution is live from this document or this PR. |
| `telegram_control_board` | Target architecture approved as read-only/status and bounded-action projections that must not become canon or bypass review gates. | Telegram notification/poller evidence exists on reviewed `main`, but the VNext Control Board and projection authority model are not live or canonical activation. |

## Supersession Map

- Exoskeleton documents: historical foundation/evidence.
- `docs/SKELETON_BUILD_PLAN.md` and
  `docs/DEVELOPMENT_DEPARTMENT_ROADMAP.md`: retained as foundation and history,
  superseded for current target architecture by
  `docs/SKELETON_ARCHITECTURE_VNEXT.md` while that file is present on reviewed
  GitHub `main`.
- Governance Contract v1: retained and absorbed as static foundation.
- #1088 Rules Rebuild: active implementation programme under the VNext
  Governance Kernel.
- #1066/PR #1070: useful design evidence only; do not merge wholesale; decompose
  onto current `main`.
- #1089 unpublished Stage 0: useful validated evidence; recreate on current
  `main` rather than recover blindly.
- #1121/#1130/#1179: memory programme absorbed into Memory and State Plane.
- #1182: Loop Engineering promoted from REVIEW candidate into this
  operator-approved architecture only while this document is present on reviewed
  GitHub `main`.
- #1253/#1254 and #1256: Home Edge programme mapped into the Execution Plane
  with current implementation status accurately marked.

## Implementation Sequence

- P0: delivery integrity and `can_publish` preflight.
- P1: official architecture consolidation.
- P2: Rules Rebuild Stage 0 on current `main`.
- P3: TaskEnvelope and Loop Controller primitives.
- P4: Approval Object, actor roles, Evidence Packet, and Data Classification
  Gate.
- P5: Memory Gateway/private canonical activation and operational/private state
  separation.
- P6: Graphify and MemPalace as independent derived pilots.
- P7: Home Edge transport review/merge, then controlled ActionPlan mutations.
- P8: Telegram/Control Board/read-only projections.
- P9: Jeeves bridge only after the above contracts are stable.

## Required Decisions

- No autonomous self-improvement.
- No agent self-approval.
- No automatic merge because tests passed.
- No direct model/Hermes/Gemini writes to canonical SQLite.
- No Graphify/MemPalace canon authority.
- No unrestricted MCP, arbitrary issue shell, or public Home Edge shell.
- No broad permanent multi-agent council.
- Use temporary bounded roles and evidence-based comparison.
- One active execution per conflicting resource set, not necessarily global
  serialization forever.
- Protected/high-risk actions remain operator-gated.

## Evidence Notes

This architecture consolidates the accepted line from Exoskeleton, managed
Development Department, Governance Contract, Rules Rebuild, controlled memory,
Loop Engineering, universal Runner work, and Home Edge work. It is
documentation-only and does not change live runtime behavior.

GitHub issue and PR references are status references for supersession and
planning. Unmerged PRs, unpublished stages, Graphify runtime, MemPalace runtime,
Home Edge controlled mutations, Telegram/Control Board VNext activation, and
Jeeves runtime work are not described here as merged or live.
