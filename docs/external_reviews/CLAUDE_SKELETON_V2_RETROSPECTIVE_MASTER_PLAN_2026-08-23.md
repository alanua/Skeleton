# Claude Review Request — Skeleton v2 Retrospective + Master Plan

Date: 2026-08-23
Repository: `alanua/Skeleton`
Mode: independent architecture review only
Do not modify repository state.

## Why you are being asked

You helped shape the Skeleton v2 direction in May 2026. We now want a retrospective from the original v2 transition to current `main`, not just another local review of one queue bug.

The operator has many parallel plans, domains, devices, products and ongoing tasks. The goal is NOT to stop expansion. The goal is to make sure every intention is captured, placed into one coherent architecture, and can be developed without losing ideas or destabilizing the core.

Please treat current GitHub state as the source of truth. Read the actual repository, issues, PRs, docs, code and current status before concluding anything. Green tests alone are not proof of semantic correctness.

## Historical control point

Start at PR #2:
- `https://github.com/alanua/Skeleton/pull/2`
- title: `Add Skeleton v2 project manifests layer`
- merged 2026-05-18

That PR established the early v2 project-manifest/state layer and the rule that `STATE.yaml` is handoff state, not absolute canon truth.

At that time the Skeleton project state said, in substance:
- Skeleton v2 lives in `alanua/Skeleton`;
- `BOOT_MANIFEST.yaml` is the v2 entrypoint candidate;
- add adapters/contracts for ChatGPT, Claude, Gemini, Runner and Codex;
- add validators and bundle readers;
- prepare bridge from the historical ChatGPT/Exoskeleton route;
- Jeeves remains separate from Skeleton Core.

Please reconstruct the architectural intent from the actual May 2026 repository/PR history instead of relying only on this summary.

## Current architecture references

Read at minimum:
- `BOOT_MANIFEST.yaml`
- `OPERATOR_RULES.yaml`
- `PROJECT_TREE.yaml`
- `CAPABILITY_REGISTRY.yaml`
- `EXECUTOR_REGISTRY.yaml`
- `MODEL_REGISTRY.yaml`
- `docs/SKELETON_ARCHITECTURE_VNEXT.md`
- `docs/DEVELOPMENT_DEPARTMENT_ROADMAP.md`
- `docs/EXECUTION_FABRIC.md`
- `docs/CONTROL_PLANE_SELF_HEALING.md`
- `docs/MEMORY_GATEWAY.md`
- `projects/skeleton/STATE.yaml`

Read these programme issues:
- #2809 — Execution Fabric v1
- #2926 — Queue 9/10 self-healing programme
- #2993 — Fabric programme unification

Also inspect the current work around:
- #3228 / PR #3230 — cross-project durable delivery
- the false-DONE repair lineage around #3202
- current Runner/Scheduler/queue implementation and tests
- current MemoryGateway/private SQLite direction
- current Dashboard/operator overview direction

Do not assume comments or prior summaries are current. Verify live GitHub state.

## Core question

From the v2 transition on 18 May 2026 to current `main`:

1. What was the original Skeleton v2 intent?
2. What has Skeleton actually become?
3. Which changes are healthy natural evolution?
4. Which changes are architectural drift, accidental complexity, premature expansion or duplicated authority?
5. Did we make any genuine wrong turn that should now be reversed rather than merely cleaned up?
6. Is the current VNext/Execution Fabric direction still faithful to the original purpose, or has the project silently changed identity?

## Important operator reality

The operator intentionally keeps expanding Skeleton because there are many ideas and real tasks across areas such as:
- development/GitHub/Runner;
- Home Edge/home automation/media/devices;
- Mail/Documents/Calendar;
- finance/Gewerbe;
- BauClock;
- Aufmass;
- travel/planning;
- private memory/knowledge;
- local and cloud LLMs;
- future Jeeves/product assistant;
- future domains not yet known.

The operator does NOT want a recommendation like “stop adding features until everything is perfect”.

The real problem is:
- new intentions must never be forgotten;
- every intention must have a place in the whole system;
- ideas must not automatically become active implementation work;
- the core must still be stabilized;
- useful current features must continue to ship;
- Skeleton must remain one coherent system rather than become a pile of parallel subsystems.

## Proposal to critique

A proposed management layer is:

### 1. Intent Registry
A canonical planning registry for all operator intentions, where a new idea can be captured immediately without becoming an executable task.

Each intent would minimally record:
- what is wanted;
- why it matters;
- domain/project;
- relationships/dependencies;
- desired outcome;
- privacy/risk class;
- lifecycle state.

Private details stay in private canonical storage; public GitHub receives only public-safe planning metadata.

### 2. System Map
One living map connecting:
`operator -> governance -> domains/projects -> control plane -> execution bindings -> nodes/services/devices -> memory/state -> projections`

Every new idea should map into this system. If it does not fit, that is evidence of a missing domain or architecture boundary.

### 3. Explicit lifecycle
Possible lifecycle:
`IDEA -> DESIGNED -> READY -> BUILDING -> CANARY -> LIVE -> STABLE`

The purpose is to stop confusing “we want this”, “code exists”, “tests passed”, “deployed”, and “actually stable”.

### 4. Two concurrent flows
- Foundation flow: queue/delivery/terminal state/watchdog/durable state/crash recovery/Execution Fabric.
- Product/domain flow: useful Home/Mail/Documents/Memory/Dashboard/etc work continues in parallel.

### 5. WIP control
Limit simultaneous active construction without limiting idea capture.
Ideas may grow without limit; BUILDING work should be bounded by resource/conflict/dependency policy.

### 6. Periodic reconciliation
Skeleton should periodically detect:
- duplicate intents/issues;
- stale PRs;
- superseded architecture;
- blocked dependencies that have cleared;
- plans with no next action;
- orphan modules;
- docs/state that no longer reflect reality.

### 7. Operator dashboard
The operator should be able to answer quickly:
- What do I want to build?
- What is being built now?
- What already works?
- What is broken?
- What needs my decision?
- What is the next meaningful milestone?

Critique this proposal aggressively. If there is a simpler or stronger design, propose it.

## Hard constraints

Preserve these unless you identify a strong reason they are wrong:
- one Skeleton control plane;
- no second Scheduler/Runner/queue/control-plane store;
- GitHub `main` remains public control/code/policy canon;
- private canonical data remains private/local;
- GitHub labels/comments should not become the only durable truth for runtime task state;
- models/workers are evidence/execution participants, not authority;
- protected/high-risk actions remain operator gated;
- tests passing never implies auto-merge;
- no hidden executor/provider fallback;
- no private data/secrets in GitHub;
- Jeeves remains separate unless there is a compelling architectural reason to change that boundary.

## Specific architecture questions

Please answer explicitly:

1. Should `Intent Registry` exist as a distinct first-class concept, or can existing project strategy/issues/state be composed to avoid another registry?
2. Where should intent authority live: GitHub, private SQLite, or split public/private records with one stable ID?
3. What is the minimum data model for an intent so it does not become another bureaucracy layer?
4. How should Intent differ from TaskEnvelope, TaskRecord, project Strategy, Issue and Memory fact?
5. What should turn an Intent into executable work?
6. What should prevent hundreds of ideas from turning into hundreds of simultaneously active Runner tasks?
7. How should dependencies be represented across domains without creating a giant brittle DAG?
8. What is the correct lifecycle for IDEA/DESIGNED/READY/BUILDING/CANARY/LIVE/STABLE, and which states are unnecessary?
9. How should `LIVE` differ from `DONE`?
10. What should be the single current-state summary mechanism so `projects/skeleton/STATE.yaml` cannot remain months stale again?
11. Should System Map be canonical data, generated projection, or both?
12. What periodic reconciliation jobs are worth automating, and which would create dangerous self-management loops?
13. How should the operator dashboard read state without becoming another authority?
14. How should the current Queue 9/10 work and Execution Fabric fit into this planning layer?
15. What should be frozen/deprecated/reversed now, if anything?

## Review for architectural drift

Actively search for:
- duplicated schedulers/queues/stores;
- duplicated routing authorities;
- planning metadata being treated as runtime truth;
- task labels becoming canonical state accidentally;
- worker/model details leaking into authority decisions;
- too-large central files with mixed responsibilities;
- modules that exist only because earlier architecture was incomplete;
- stale docs still presented as active canon;
- project/domain boundaries that no longer match reality;
- parallel memories or indexes competing with canonical SQLite;
- operational features that bypass common governance/control fabric;
- “temporary” compatibility paths that have silently become permanent.

For each drift item classify:
- KEEP
- CONSOLIDATE
- DEPRECATE
- REMOVE
- REBUILD
- NEEDS_EVIDENCE

## Stabilization without stopping growth

Design a practical operating model for the next 1–3 months that allows:
- continuous capture of new ideas;
- continued useful feature delivery;
- active core stabilization;
- bounded WIP;
- visible priorities;
- architectural convergence;
- no idea loss;
- no parallel control planes.

Do not give only abstract principles. Give an implementable sequence.

## Compare against current programme

Assess whether the following current direction is correct:

1. finish cross-project durable delivery (#3228/#3230);
2. refresh/merge the current-main false-DONE repair lineage (#3202 successor);
3. wire an independent poller heartbeat/watchdog to the existing `long_lived_poller_reload` recovery action, without a second Runner;
4. introduce durable SQLite TaskRecord/ExecutionAttempt initially as shadow state;
5. require at least one real controlled poller crash/restart with zero divergence before authority flip;
6. make SQLite task/attempt state authoritative and GitHub labels a projection;
7. incrementally decompose the large poller without rewrite;
8. continue Execution Fabric integration after queue semantics are trustworthy.

Say what you would reorder, reject, combine or simplify.

## Desired output

Use this structure:

### VERDICT
One of:
- ON_COURSE
- ON_COURSE_WITH_DRIFT
- STRUCTURAL_CORRECTION_NEEDED
- MAJOR_RETHINK

### ORIGINAL V2 INTENT
What v2 was trying to become in May 2026.

### WHAT SKELETON IS NOW
Short accurate description of current real system.

### HEALTHY EVOLUTION
What changed for good reasons.

### WRONG TURNS / DRIFT
Concrete evidence-backed deviations.

### WHAT NOT TO REVERSE
Important growth that may look complex but should be preserved.

### INTENT / MASTER-PLAN DESIGN
Your preferred solution for never losing operator ideas while keeping one coherent system.

### MINIMAL DATA MODEL
Exact fields/entities and authority boundaries.

### STATE / LIFECYCLE MODEL
Define states and transitions.

### SYSTEM MAP
What the map should contain and whether it is canonical/generated.

### WIP / PRIORITY MODEL
How to continue expansion without saturating the control plane.

### RECONCILIATION LOOP
What Skeleton should periodically verify and repair/propose.

### CURRENT CORE STABILIZATION
Critique and corrected ordering of #3228/#3202/watchdog/SQLite/Fabric work.

### 30-DAY PLAN
Concrete sequence.

### 90-DAY TARGET
What “Skeleton is under control” should objectively mean.

### DEPRECATE / CONSOLIDATE LIST
Specific current mechanisms/files/programmes to simplify or retire.

### TOP 5 RISKS
The biggest risks if we continue at current speed.

### FIRST NEXT STEP
One smallest high-leverage action after the current protected queue work.

## Review style

Be adversarial and concrete. Do not agree with the proposal merely because it is presented here. Prefer simplification over adding abstractions. Distinguish architecture from what is actually live. Cite exact files/issues/PRs where possible. If current GitHub evidence contradicts this request, trust current GitHub evidence and explain the discrepancy.
