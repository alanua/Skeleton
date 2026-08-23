# Claude independent audit — Skeleton reuse-first / external component architecture

Date: 2026-08-23
Mode: READ-ONLY ARCHITECTURE / COMPONENT / LICENSING AUDIT
Repository: `alanua/Skeleton`
Pinned baseline: `main@53ee95215f903be0684eadee0f70aae3ab43c370`

## Operator context — important

Skeleton is a **private personal system for one operator/family**, not a commercial SaaS/product and not intended for resale.

Therefore licensing analysis must be practical for **private/internal self-hosted use**:

- may we legally run it privately;
- may we modify it for our own use;
- may we call it through API/SDK/service boundaries;
- what attribution/source obligations still apply;
- what restrictions matter only if Skeleton is later distributed, hosted for third parties, or commercialized;
- what creates undesirable technical/vendor lock-in even if legally usable privately.

Do **not** reject a useful source-available/fair-code component merely because it would be awkward for commercial resale. Flag that distinction explicitly.

At the same time, do not copy/vend code casually. Prefer upstream service/API/SDK boundaries where possible.

---

# Objective

A prior retrospective concluded that Skeleton is broadly `ON_COURSE_WITH_DRIFT`, not in need of a fundamental rethink.

Now answer a narrower practical architecture question:

> Where is Skeleton implementing commodity infrastructure itself that should instead reuse, embed, adapt, or learn from mature external projects/components — while preserving Skeleton as the single human-controlled authority/control plane?

We want to reduce custom code, maintenance burden and reinvention **without** importing a second Scheduler, Runner, queue authority, memory authority, approval authority, or competing control plane.

This is a second-pass architecture review, not an implementation task.

---

# Previous Skeleton retrospective context

Start with the prior Claude task if useful:

https://github.com/alanua/Skeleton/blob/external/claude-v2-retrospective-master-plan-20260823/docs/external_reviews/CLAUDE_SKELETON_V2_RETROSPECTIVE_MASTER_PLAN_2026-08-23.md

Historical v2 starting point:

- PR #2 — Skeleton v2 project manifests layer:
  https://github.com/alanua/Skeleton/pull/2

Accepted strategic direction from the previous pass, subject to your independent verification:

- Skeleton is broadly on-course with execution/reliability drift;
- do not create a second Scheduler, Runner, queue state authority, memory authority, or control plane;
- stabilize queue/execution while preserving useful domain development;
- reuse existing planning/intent structures rather than create a parallel master registry;
- keep intent lifecycle and executor/model binding maturity as different typed state axes;
- use external systems behind Skeleton authority, not as replacements for operator/governance authority.

---

# 1. Evidence discipline

Use a local clone plus `git`, `rg`, tests/static inspection, and authenticated `gh` where available. Read exact files/commits/issues/PRs rather than trusting summaries.

Use these state classes:

- `LIVE_VERIFIED` — current runtime evidence proves active/healthy;
- `MERGED_NOT_RUNTIME_VERIFIED` — code is merged but runtime not proven;
- `OPEN_PR` — implementation exists only in an unmerged PR;
- `PLANNED` — issue/design only;
- `HISTORICAL_OR_SUPERSEDED` — no longer current;
- `UNKNOWN_NEEDS_RUNTIME_EVIDENCE` — cannot be established from repository evidence.

Never promote `runner:done`, green tests, a deployment package, old approval, or an open PR into `LIVE_VERIFIED` without runtime evidence.

If current `main` has advanced beyond the pinned baseline, report both:

1. pinned baseline `53ee95215f903be0684eadee0f70aae3ab43c370`;
2. actual current `main` SHA reviewed.

Do not modify files, create PRs, merge, deploy, install software, activate workflows, access secrets, or write private data.

---

# 2. Non-negotiable Skeleton authority boundary

External components may provide:

- SDKs/libraries;
- agent harnesses;
- sandboxes/runtimes;
- workflow execution;
- connectors;
- durable-execution primitives;
- graph/catalog data models;
- UI components;
- source/data adapters;
- testing/evaluation infrastructure.

They must not silently become competing authority for:

- operator intent;
- governance/policy;
- privacy classification;
- approvals;
- TaskContract / TaskEnvelope semantics;
- ExecutionBinding / RouteLease authority;
- terminal DONE acceptance;
- canonical task/attempt state;
- MemoryGateway/private canonical memory;
- protected GitHub merge/deploy authority.

Explicit anti-pattern to detect:

`reuse component -> import its scheduler/state/approval semantics -> accidentally create a second Skeleton inside Skeleton`.

---

# 3. Current Skeleton architecture/evidence to read

Pinned repository tree:

https://github.com/alanua/Skeleton/tree/53ee95215f903be0684eadee0f70aae3ab43c370

Core control/registry files:

- BOOT_MANIFEST:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/BOOT_MANIFEST.yaml
- OPERATOR_RULES:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/OPERATOR_RULES.yaml
- CAPABILITY_REGISTRY:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/CAPABILITY_REGISTRY.yaml
- EXECUTOR_REGISTRY:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/EXECUTOR_REGISTRY.yaml
- MODEL_REGISTRY:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/MODEL_REGISTRY.yaml
- PROVIDER_ROUTING:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/PROVIDER_ROUTING.yaml

Architecture/docs:

- SKELETON_ARCHITECTURE_VNEXT.md:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/docs/SKELETON_ARCHITECTURE_VNEXT.md
- SKELETON_ARCHITECTURE_VNEXT.yaml:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/docs/SKELETON_ARCHITECTURE_VNEXT.yaml
- DEVELOPMENT_DEPARTMENT_ROADMAP.md:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/docs/DEVELOPMENT_DEPARTMENT_ROADMAP.md
- projects/skeleton/STATE.yaml:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/projects/skeleton/STATE.yaml

Execution/runtime code to inspect directly:

- `core/execution_fabric.py`:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/core/execution_fabric.py
- `core/runner_codegen_router.py`:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/core/runner_codegen_router.py
- `core/executor_registry.py`:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/core/executor_registry.py
- `core/model_registry.py`:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/core/model_registry.py
- Runner poller:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/scripts/runner_poll_github_tasks.py

Core architecture/programme issues:

- #1750 — general modular architecture:
  https://github.com/alanua/Skeleton/issues/1750
- #1752 — full canon/private-memory audit:
  https://github.com/alanua/Skeleton/issues/1752
- #2809 — Execution Fabric v1:
  https://github.com/alanua/Skeleton/issues/2809
- #2926 — Queue 9/10 / self-healing architecture:
  https://github.com/alanua/Skeleton/issues/2926
- #2993 — Fabric programme unification:
  https://github.com/alanua/Skeleton/issues/2993
- #3228 — cross-project durable delivery:
  https://github.com/alanua/Skeleton/issues/3228
- PR #3230 — closed DO_NOT_MERGE implementation:
  https://github.com/alanua/Skeleton/pull/3230
- #3234 — replacement crash-safe existing-PR receipt replay:
  https://github.com/alanua/Skeleton/issues/3234

Important current repair context:

#3234 exists because review found a real crash window in #3230:

`target PR created -> process crashes before durable receipt -> retry sees existing PR -> canonical source receipt may not be reconstructable -> source task may remain waiting forever`.

Do **not** recommend swapping queue/execution engines in the middle of this repair. Evaluate OSS reuse as the next-stage architecture decision.

---

# 4. OpenHands — already integrated, not hypothetical

Relevant Skeleton history:

- bounded Codex quota/provider fallback to OpenHands:
  https://github.com/alanua/Skeleton/commit/29aa1aeff0a0b254bad29fa67309c974052b18a2
- Bitwarden/OpenRouter credential binding for OpenHands:
  https://github.com/alanua/Skeleton/commit/45c155a14afdf4d34dbb07539cdff02807c3dcdb
- explicit OpenHands secondary route with Kimi:
  https://github.com/alanua/Skeleton/commit/ef002c22f3fae1d674e054e15b5366d7bc90e492
- current route:
  https://github.com/alanua/Skeleton/blob/53ee95215f903be0684eadee0f70aae3ab43c370/core/runner_codegen_router.py

Current Skeleton route includes, subject to exact code verification:

- executor id `openhands-external`;
- Skeleton-owned `ExecutionBinding` and `RouteLease`;
- registered credential binding;
- OpenRouter/Kimi runtime mapping;
- bounded budget/iterations/retries;
- CLI execution via `openhands --headless --json`.

This does **not** prove OpenHands runtime is currently installed/healthy on every expected node. Classify runtime separately.

Upstream sources:

- organization:
  https://github.com/OpenHands
- OpenHands:
  https://github.com/OpenHands/OpenHands
- Software Agent SDK:
  https://github.com/OpenHands/software-agent-sdk
- SDK license — verify current state:
  https://github.com/OpenHands/software-agent-sdk/blob/main/LICENSE
- SDK introduction:
  https://www.openhands.dev/blog/introducing-the-openhands-software-agent-sdk
- docs:
  https://docs.openhands.dev/

Deep-review at minimum:

- agent harness;
- SDK/API boundary;
- sandbox / Agent Server / workspace isolation;
- tool abstractions;
- repository/context handling;
- event stream / trajectory / structured evidence;
- evaluation infrastructure;
- sub-agent/critic facilities if current upstream really provides them;
- model/provider abstraction;
- retry/recovery behavior;
- local/self-hosted operation without OpenHands Cloud/Enterprise;
- exact license boundaries of components.

Main question:

> Is Skeleton currently using only a shallow slice of OpenHands by treating it mainly as a secondary CLI executor? If yes, which exact components should Skeleton reuse via SDK/service/pattern — without importing OpenHands control-plane authority?

Do **not** assume “make OpenHands canonical” is correct. Reject it if it creates coupling, duplicates Execution Fabric, or weakens deterministic gates.

---

# 5. n8n — already designed, deployment status must be proven

Skeleton evidence:

- #1553 — isolated n8n Community as bounded workflow executor:
  https://github.com/alanua/Skeleton/issues/1553
- #1571 — dedicated SQLite deployment package design:
  https://github.com/alanua/Skeleton/issues/1571
- #1572 — Hetzner runtime deployment task:
  https://github.com/alanua/Skeleton/issues/1572
- #1629 — hardened package rebuild:
  https://github.com/alanua/Skeleton/issues/1629
- PR #1632 — n8n package; at request preparation it was open/draft/unmerged, therefore not live proof:
  https://github.com/alanua/Skeleton/pull/1632
- #1543 — unified visual intake with n8n bounded runtime role:
  https://github.com/alanua/Skeleton/issues/1543
- #1564 — Diagram/Workflow Steward -> inactive n8n draft bridge:
  https://github.com/alanua/Skeleton/issues/1564
- #2223 — Operations Dashboard including n8n workflow status:
  https://github.com/alanua/Skeleton/issues/2223

n8n upstream:

- repository:
  https://github.com/n8n-io/n8n
- current license:
  https://github.com/n8n-io/n8n/blob/master/LICENSE.md
- Sustainable Use License explanation:
  https://docs.n8n.io/privacy-and-security/sustainable-use-license/
- self-hosting docs:
  https://docs.n8n.io/hosting/

Licensing must be judged under the actual operator context: **private personal self-hosted use, no resale/SaaS**. Clearly separate what is allowed for our use from what would become a problem only under future redistribution/commercialization.

Evaluate specifically whether n8n should handle:

- Gmail/Microsoft Mail transport glue;
- Google/Microsoft Calendar integrations/mirrors;
- Telegram/webhooks;
- document-intake glue;
- deterministic cross-service workflow steps;
- API retries/idempotency;
- credential integrations;
- visual workflow authoring;
- inactive workflow drafts generated from confirmed WorkflowSpec.

And identify what must **not** move into n8n:

- Skeleton operational Scheduler authority;
- canonical task state;
- approval authority;
- MemoryGateway writes outside approved adapters;
- Runner/GitHub protected execution authority;
- arbitrary shell/SSH/Docker/device authority.

Main question:

> Which existing/future Skeleton workflows should n8n execute because it is already better at commodity integration glue, and which native Skeleton semantics must stay outside it to avoid a second control plane?

---

# 6. Temporal

Upstream:

- docs:
  https://docs.temporal.io/
- organization:
  https://github.com/temporalio
- server:
  https://github.com/temporalio/temporal

Evaluate against #2926/#3234:

- event history;
- deterministic replay;
- activity boundaries;
- retries/timeouts;
- signals/queries;
- crash recovery;
- idempotency;
- workflow-level guarantees vs activity at-least-once reality;
- observability and recovery tooling.

Answer:

> Should Skeleton (A) only adopt Temporal semantics in our SQLite execution history, (B) later use Temporal runtime behind Skeleton, or (C) avoid Temporal entirely?

Do not optimize for elegance alone. Include migration/ops complexity and private single-operator scale.

---

# 7. LangGraph

Upstream:

- repository:
  https://github.com/langchain-ai/langgraph
- license:
  https://github.com/langchain-ai/langgraph/blob/main/LICENSE
- docs:
  https://docs.langchain.com/oss/python/langgraph/overview

Evaluate whether it provides genuinely useful bounded AI-subflow checkpoints/human interrupts/state handling that Skeleton lacks, or whether it would simply introduce a second state machine.

Explicitly decide `USE_AS_LIBRARY_SDK | ADAPT_PATTERN | DO_NOT_USE | DEFER`.

---

# 8. Backstage

Upstream:

- repository:
  https://github.com/backstage/backstage
- overview:
  https://backstage.io/docs/overview/what-is-backstage/
- software catalog/system model:
  https://backstage.io/docs/features/software-catalog/system-model/

Evaluate mainly as a **data-model donor** for a generated Skeleton System Map:

`Domain -> System -> Component -> Capability -> Adapter/Executor -> Runtime/Device -> Data/Memory`

Prefer adopting entity/relation/catalog patterns over deploying Backstage unless deployment has a concrete benefit for one private operator.

---

# 9. Home Assistant

Upstream:

- core:
  https://github.com/home-assistant/core
- official site:
  https://www.home-assistant.io/

Treat Home Assistant as a specialized domain subsystem Skeleton should normally orchestrate rather than reimplement.

Define exact authority boundary:

- which device/entity/state automation belongs to HA;
- which cross-domain intent/policy/approval belongs to Skeleton;
- which Home Edge execution remains outside HA;
- how Skeleton should consume HA without duplicating its device model.

---

# 10. Ollama / local model serving

Upstream:

- repository:
  https://github.com/ollama/ollama
- official site:
  https://ollama.com/

Evaluate only as local model-serving infrastructure.

It must not become:

- task authority;
- model-selection policy authority;
- executor authority;
- memory authority.

Check whether Skeleton's model/executor registries should treat Ollama as one provider endpoint among others and whether any current custom model-serving code is redundant.

---

# 11. MCP

Upstream:

- specification repository:
  https://github.com/modelcontextprotocol/modelcontextprotocol
- docs:
  https://modelcontextprotocol.io/

Evaluate MCP as protocol/adapter infrastructure, **not** a trust boundary.

Identify:

- where MCP cleanly reduces custom adapter glue;
- where unrestricted MCP tools would violate typed Skeleton capabilities;
- how MCP tool exposure should bind to Skeleton ActionGate/ExecutionBinding/privacy.

---

# 12. Existing reuse-first intent / historical OSS donors

Search current Skeleton and historical `alanua/jeeves` for prior decisions/research around:

- OpenHands / OpenDevin;
- SWE-agent;
- Aider;
- Goose;
- Letta;
- LangGraph;
- MCP;
- n8n;
- Home Assistant;
- World Monitor;
- Graphify/MemPalace/Cognee or similar memory projects;
- any `RESEARCH_EXTERNAL_LLM_HARNESS_PATTERNS` or equivalent historical material.

Older Jeeves repo:

https://github.com/alanua/jeeves

Existing World Monitor reuse example:

- #1545:
  https://github.com/alanua/Skeleton/issues/1545

Classify old choices as:

`CURRENT | ABSORBED | SUPERSEDED | USEFUL_REFERENCE | REJECTED`.

Do not wholesale resurrect old architecture.

---

# 13. You may add candidates — but only for a real Skeleton gap

If another mature project clearly solves an existing Skeleton capability better, add it. Examples might include workflow engines, observability, credential brokers, durable queues, graph/catalog systems, local inference, document processing, or agent runtimes.

But do **not** produce a generic “top OSS AI tools” list.

For every added candidate state:

- exact Skeleton problem it solves;
- current Skeleton code/issue it could replace or simplify;
- why the candidate is mature enough;
- authority/privacy boundary;
- operational and license implications.

---

# 14. Required decision method

For every candidate/component choose exactly one primary recommendation:

- `USE_AS_SERVICE` — run upstream largely unchanged behind Skeleton adapter;
- `USE_AS_LIBRARY_SDK` — import stable upstream library/SDK;
- `ADAPT_PATTERN` — copy semantics/design, not implementation;
- `VENDOR_BOUNDED_COMPONENT` — vendor a small reviewed component only if service/library use is impossible and license permits;
- `KEEP_CURRENT_SKELETON` — our implementation is strategically correct;
- `REPLACE_SKELETON_COMPONENT` — external component is materially better and migration is justified;
- `DO_NOT_USE` — bad fit/security/complexity/authority issue;
- `DEFER` — useful later, not now.

Score each on:

- architectural fit;
- custom-code reduction;
- operational maturity;
- security/privacy fit;
- license fit for private personal use;
- cost/API dependency;
- offline/local viability;
- maintenance burden;
- migration complexity;
- lock-in/reversibility;
- failure isolation;
- observability/evidence quality.

Do not optimize only for LOC reduction. Saving code while importing a hidden second authority is a net loss.

---

# 15. Mandatory questions

Answer directly:

1. What parts of Skeleton are strategic core semantics and should remain ours?
2. What parts are commodity infrastructure Skeleton should stop reimplementing?
3. Is current OpenHands integration too shallow? Exactly what should change, if anything?
4. Can OpenHands SDK/runtime/sandbox reduce custom executor code without weakening Execution Fabric?
5. Which OpenHands components are free/local/self-hostable for our private use, and which require paid model/cloud/enterprise dependencies?
6. Which n8n capabilities should we actually use, given Skeleton is private/non-commercial?
7. Where would n8n become an unacceptable second scheduler/state/approval authority?
8. Are we reinventing Temporal-like durable execution? If yes, what should we borrow versus deploy?
9. Does LangGraph solve a real missing problem, or should Skeleton avoid it?
10. Should Backstage be deployed, or should we only adapt its catalog/entity/relation model?
11. Which Home/Home Edge behavior should be delegated to Home Assistant rather than implemented in Skeleton?
12. Where can MCP remove custom glue safely?
13. What existing Skeleton modules/code can eventually be deleted or simplified if reuse is adopted?
14. What current/planned work should be cancelled as unnecessary reinvention?
15. What should we **not** touch until Queue 9/10/#3234 stabilization is complete?

---

# 16. Required output matrix

Produce a concrete table with at least these columns:

| Skeleton capability/problem | Current implementation/state | External candidate/component | Reuse mode | Private-use license status | Cost/runtime needs | Authority risk | Migration cost | Expected custom-code reduction | Verdict | Evidence |

Use exact files/issues/PRs/commits/upstream refs.

Then produce a second table:

| Current Skeleton code/module | Keep | Simplify | Replace | Delete later | Why | Dependency/gate |

Do not recommend deletion without proving equivalent behavior and migration path.

---

# 17. Required architecture output

Provide a target diagram showing Skeleton after reuse, with authority clearly remaining at Skeleton.

Expected conceptual shape, challenge if wrong:

```text
Operator
   |
Skeleton Governance / Intent / Privacy / Approval
   |
Scheduler / Loop / Execution Fabric / MemoryGateway
   |
+--------------------+----------------------+-------------------+
|                    |                      |                   |
OpenHands adapter    n8n adapter            HA adapter          MCP adapters
(agent work)         (integration glue)     (home domain)       (tools/data)
|                    |                      |                   |
OpenHands runtime    n8n workflows          Home Assistant      external systems

Temporal semantics/runtime only where justified
Ollama only as model provider
Backstage model only as System Map donor unless deployment is justified
```

For each external component label whether Skeleton owns:

- scheduling;
- routing;
- approval;
- credentials reference;
- side-effect permission;
- terminal validation;
- durable state;
- canonical memory.

---

# 18. Required phased plan

Give an actionable phased migration plan that does **not** derail current P0 queue stabilization.

At minimum:

## Phase A — now / read-only

- inventory runtime and repository evidence;
- component audit;
- no authority changes;
- no installs/deploys required to complete architecture decision.

## Phase B — after #3234 / Queue stabilization gate

- smallest high-value reuse pilot;
- reversible;
- one component only;
- measurable before/after metrics.

## Phase C — controlled adoption

- adapter boundary;
- migration tests;
- failure isolation;
- fallback/rollback;
- remove duplicate Skeleton code only after live proof.

## Phase D — cleanup

- delete superseded custom code;
- update capability/System Map/state documentation;
- preserve historical evidence.

Provide `FIRST 3 PILOTS`, ordered by value/risk.

---

# 19. Runtime inventory requirement

Repository evidence already suggests that “planned/packaged” has sometimes been confused with “installed/live”. Produce a runtime verification checklist for at least:

- OpenHands;
- n8n;
- Ollama/local models;
- Home Assistant;
- MCP services;
- Graphify/MemPalace/Cognee-related components if present;
- Runner/Scheduler/Loop;
- any other reused OSS runtime you discover.

For each specify the minimal evidence required to call it:

`INSTALLED -> RUNNING -> HEALTHY -> INTEGRATED -> PRODUCTION_ELIGIBLE`.

Do not execute runtime checks in this audit unless the environment explicitly gives you read-only runtime access; otherwise produce the exact checklist only.

---

# 20. Final verdict format

Required sections:

1. `VERDICT`
2. `WHAT SKELETON MUST OWN`
3. `WHAT SKELETON SHOULD STOP BUILDING`
4. `CURRENT OSS/RUNTIME INVENTORY`
5. `OPENHANDS DEEP REVIEW`
6. `N8N DEEP REVIEW`
7. `TEMPORAL DECISION`
8. `LANGGRAPH DECISION`
9. `BACKSTAGE/SYSTEM MAP DECISION`
10. `HOME ASSISTANT BOUNDARY`
11. `OLLAMA/MODEL SERVING BOUNDARY`
12. `MCP BOUNDARY`
13. `OTHER HIGH-VALUE CANDIDATES`
14. `REUSE MATRIX`
15. `SKELETON CODE TO KEEP/SIMPLIFY/REPLACE/DELETE-LATER`
16. `TARGET ARCHITECTURE`
17. `FIRST 3 PILOTS`
18. `30-DAY REUSE PLAN`
19. `90-DAY TARGET`
20. `TOP 5 RISKS`
21. `WHAT NOT TO CHANGE BEFORE QUEUE 9/10`
22. `FIRST NEXT STEP`

End with one short answer to this exact question:

> **If you were designing the next Skeleton phases today, knowing the entire current repository/history and that Skeleton is a private personal system, which mature external components would you reuse instead of writing our own — and exactly where would you draw the boundary so Skeleton remains one coherent human-controlled system?**

---

# 21. Output handling

Do not modify Skeleton.

Return the report as one Markdown file, preferably named:

`CLAUDE_SKELETON_OSS_REUSE_AUDIT_2026-08-23.md`

If working in Claude Code with a local clone, save the report outside `main` or provide its exact local path/content to the operator. If you need to put the report in GitHub, use a separate review-only branch and state the exact branch/commit; do not open a merge PR unless separately asked.
