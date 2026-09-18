# Skeleton

**A model-neutral control plane for safe, auditable AI-assisted engineering and edge execution.**

Skeleton turns an assistant session into a bounded engineering workflow: load declared context, route work to the right project, pass mutations through explicit approval/execution boundaries, and verify what actually changed.

It is used by its primary maintainer for real cross-repository engineering and Home Edge operations. Public adoption is still early; the repository does not claim third-party deployment numbers it cannot substantiate.

```text
Human intent
   -> project/context routing
   -> AI or human worker
   -> approval / risk gate
   -> registered Runner / executor
   -> repository, service, or edge action
   -> independent verification
   -> audit + rollback state
```

### At a glance

- **Model-neutral:** Codex, ChatGPT, Gemini, local models, or human workers can sit behind the same control contracts.
- **Bounded execution:** privileged work goes through registered identities, lanes, timeouts, and approval rules.
- **Verifiable outcomes:** Skeleton distinguishes a command being sent from the intended state being independently verified.
- **Cross-repository:** project routing and exact revision contracts keep canonical source ownership explicit.
- **Real edge boundary:** the same patterns cover software repositories and Home Edge / physical-device workflows.
- **Open source:** Apache-2.0, with public security, contribution, maintenance, and governance documentation.

**New here?** Run the safe [5-minute quickstart](docs/QUICKSTART.md), then read [project impact](docs/PROJECT_IMPACT.md) and the [threat model](docs/THREAT_MODEL.md).

## Why Skeleton exists

LLM-assisted systems become difficult to trust when context, permissions, execution, memory, and recovery live only in chat. Skeleton makes those concerns explicit and inspectable.

Core principles:

1. **Declared context** — boot from canonical manifests instead of reconstructing state from conversation history.
2. **Model neutrality** — keep ChatGPT, Codex, Gemini, local models, runners, and other agents behind explicit role contracts.
3. **Read before write** — durable mutations require current state and the correct control path.
4. **Bounded execution** — actions flow through registered executors and approval/risk gates rather than ad-hoc shell access.
5. **Auditable outcomes** — distinguish sent, accepted, applied, and independently verified state.
6. **Durable continuity** — route semantic memory, registries, live state, and audit evidence to the right stores.
7. **Recoverability** — preserve rollback, failure history, and the next safe action.

## What is in this repository

Skeleton Core currently includes:

- manifest-driven boot and project routing;
- capability, provider, executor, helper, and command registries;
- ActionGate / approval-oriented execution patterns;
- memory-routing and audit-ledger contracts;
- Runner and runtime-maintenance infrastructure;
- Home Edge / physical-device control patterns;
- generic Android/Home Edge control contracts, while media-domain implementation lives in `alanua/skeleton-media`;
- public-safe Aufmass/CAD pipeline specifications and adapters;
- CI/runtime workflows for bounded validation and recovery.

The repository is intentionally broader than a single application: it is the control plane used to build and maintain several real systems without collapsing all authority into one autonomous agent.

## Repository identity

```text
alanua/Skeleton        = Skeleton Core repository
alanua/skeleton-media = separate public media subsystem for Home Edge
alanua/jeeves          = separate runtime/product repository and historical migration source
```

Historical prototypes may be referenced as evidence, but are not canonical runtime authority.

## Current status

```text
Status: ACTIVE_CONTROLLED_BOOTSTRAP
Active route: BOOT_MANIFEST.yaml
Current entrypoint: BOOT_MANIFEST.yaml
```

The project is under active development. Interfaces and registries may evolve while safety, auditability, and continuity rules are kept explicit.

## Start here

For a no-credentials, no-deployment verification path, use [docs/QUICKSTART.md](docs/QUICKSTART.md).

For reviewers evaluating the project:

- [docs/PROJECT_IMPACT.md](docs/PROJECT_IMPACT.md) — current real-world use and maintainer workload
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — security boundaries and why agent-to-runtime execution needs explicit controls
- [docs/AI_ASSISTED_MAINTENANCE.md](docs/AI_ASSISTED_MAINTENANCE.md) — human-controlled AI maintenance model

Canonical control-plane entrypoints:

- `BOOT_MANIFEST.yaml` — canonical boot/context entrypoint
- `PROJECT_INDEX.yaml` / `PROJECT_TREE.yaml` — project structure
- `CAPABILITY_REGISTRY.yaml` — declared capabilities
- `EXECUTOR_REGISTRY.yaml` — registered execution paths
- `PROVIDER_ROUTING.yaml` — provider/model routing
- `MEMORY_ROUTING.yaml` — memory trust/privacy routing
- `OPERATOR_RULES.yaml` — operator and mutation rules
- `docs/ACTION_GATE.md` — action-gating model
- `docs/AUDIT_LEDGER.md` — audit evidence model

## Maintainer workflow

Skeleton is maintained through issue triage, scoped branches/PRs, review, CI/runtime validation, exact-head verification, and bounded deployment/activation steps. AI tools may assist with research, implementation, tests, and review, but merges and security-sensitive actions remain maintainer-controlled.

See:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)
- [MAINTAINERS.md](MAINTAINERS.md)
- [docs/AI_ASSISTED_MAINTENANCE.md](docs/AI_ASSISTED_MAINTENANCE.md)

## Security model

Skeleton deliberately separates normal code changes from actions that can affect credentials, infrastructure, physical devices, networks, firmware, or user data. Such operations require the registered control path and the appropriate approval boundary.

Do not publish secrets, private device topology, credentials, tokens, private keys, or sensitive runtime state in issues or pull requests. See [SECURITY.md](SECURITY.md).

## Core rule

```text
Manifest controls behavior.
Markdown explains behavior.
Tooling verifies behavior.
Tests preserve behavior.
```

## Public-safe Aufmass docs

```text
docs/AUFMASS_PRIVATE_WORKSPACE_CONTRACT.md
docs/AUFMASS_PRIVATE_PILOT_PROTOCOL.md
docs/AUFMASS_SOURCE_PACK.md
```

These documents define the public/private boundary for bounded private pilots and source-pack intake.

## License

Licensed under the [Apache License 2.0](LICENSE).
