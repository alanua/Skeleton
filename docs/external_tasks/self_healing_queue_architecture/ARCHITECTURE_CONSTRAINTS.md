# ARCHITECTURE CONSTRAINTS — non-negotiable Skeleton boundaries

## Authority

- Current GitHub state is the public repository source of truth for code/PR/issue state.
- Scheduler owns when/what becomes runnable.
- Execution Fabric owns selection of an already-authorized `ExecutionBinding`.
- Runner/control core owns attempt lifecycle and deterministic completion validation.
- A model never gains execution authority.
- An executor never chooses arbitrary provider/model/endpoint/credentials from task prose.
- Passing tests alone never grants merge or DONE.

## One control plane

Do not create a second Scheduler, second Runner, second queue, second policy engine, second credential broker, or hidden bypass lane.

You may propose better internal tables/components, but they must remain one coherent authority graph.

## Task authority

Task authority comes from typed task contract + code-owned policy/gates.

Free-form issue/chat text may describe intent, but must not be able to weaken or select:

- privacy class;
- risk/side-effect class;
- executor/provider/model;
- endpoint/host/path/command;
- credentials;
- budget;
- validation contract;
- operator approval requirement.

## ExecutionBinding

Executors and models are separate registries/dimensions but are routed atomically as a compatible binding.

Supported conceptual kinds:

- `NO_MODEL`
- `EMBEDDED_MODEL`
- `EXTERNAL_MODEL`

A retry/reroute may only choose another binding that already passes capability, privacy, authority, credential, health, budget and side-effect gates.

## Completion

`rc`, provider text, model self-report, test status, or PR existence are attempt evidence only.

`DONE` must belong to deterministic deliverable validation against the typed contract.

Protected/high-risk tasks that otherwise pass remain `NEEDS_OPERATOR` before final merge/deploy/mutation.

## Protected surfaces

Changes touching these areas are protected/high-risk and require exact-head operator approval before merge/activation:

- `BOOT_MANIFEST.yaml`
- `PROJECT_TREE.yaml`
- `OPERATOR_RULES.yaml`
- `CAPABILITY_REGISTRY.yaml`
- `.github/workflows/`
- `scripts/runner_poll_github_tasks.py`
- `core/gate_engine.py`
- `core/action_gate.py`
- Runner core / child environment / adapter boundaries
- secrets / credential runtime boundaries
- deploy / server
- finance / legal / governance

Architecture may recommend changes there, but automatic merge is forbidden.

## Privacy

Skeleton is local-first for private data.

- private documents, customer data, quantities, local paths, secret values and private runtime evidence stay local/private;
- GitHub should receive only public-safe aggregate status, hashes, opaque references, reason classes and code/tests;
- cloud reroute is forbidden when privacy class requires local execution;
- failure recovery never broadens privacy scope.

## Credentials

Canonical controller-side secret flow is conceptually:

`device/service -> CredentialRef -> CredentialBroker/SecretStore -> approved bounded action -> ephemeral secret material -> consumer`

No generic `get_secret`, arbitrary caller-selected env/path/host/output, or new credential DB should be introduced.

## Side effects

Retry policy must distinguish read-only/replay-safe actions from ambiguous or irreversible mutations.

For ambiguous mutating outcomes:

- fixed readback/reconciliation is preferred;
- blind automatic replay is forbidden;
- if outcome cannot be established safely, escalate to operator.

## Queue behavior

- one blocked task must not stop unrelated eligible work;
- fairness across domains is required;
- recovery itself must be bounded;
- queue-idle recovery must be race-safe;
- stale labels must be treated as projections, not canonical state;
- restart/reboot must recover from durable local state and receipts;
- routine recovery should be quiet; durable operator-required states should generate attention.

## Health

Executor and model/provider health are distinct.

Initial health decisions must be deterministic and evidence-based, not learned from model self-report. Simple states such as `LIVE / DEGRADED / COOLDOWN / DISABLED` are acceptable. Adaptive ranking may be proposed only after sufficient verified evidence and must not weaken hard eligibility gates.

## Technology constraints

The migration should fit the current stack unless there is a strong reason otherwise:

- Python;
- SQLite durable local state;
- systemd on Runner/Home Edge hosts;
- GitHub issues/PRs as public operator/project surface;
- existing Scheduler/shared-dispatch fabric;
- existing bounded maintenance executors and Home Edge signed executor.

A recommendation to add a technology is acceptable only if it removes a concrete failure mode and does not create another control plane.
