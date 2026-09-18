# Skeleton

[![License: Apache-2.0](https://img.shields.io/github/license/alanua/Skeleton)](LICENSE)
[![Skeleton Media contract](https://github.com/alanua/Skeleton/actions/workflows/skeleton-media-contract.yml/badge.svg)](https://github.com/alanua/Skeleton/actions/workflows/skeleton-media-contract.yml)
[![Container package validation](https://github.com/alanua/Skeleton/actions/workflows/container-package-validation.yml/badge.svg)](https://github.com/alanua/Skeleton/actions/workflows/container-package-validation.yml)

Skeleton is a **model-neutral control plane for safe, auditable AI-assisted engineering and edge execution**.

It is designed for the point where coding agents stop being chat helpers and begin touching repositories, services, private runtime state, or physical devices. Skeleton keeps authority outside the model: context is declared, permissions are explicit, execution is bounded, mutations are approval-gated, outcomes are audited, and important state is independently verified.

The project is actively used by its primary maintainer across multiple public repositories and a real Home Edge deployment. It is not a demo harness: the same control model is used for PR review, issue routing, runtime maintenance, cross-repository work, rollback, and device-facing operations.

## The core idea

    Human / repository policy
              |
              v
    Declared context + project routing
              |
              v
    Privacy / capability / approval gates
              |
              v
    Runner or registered executor
              |
              v
    Repository / service / Home Edge action
              |
              v
    Audit receipt + independent verification

Models can propose and execute bounded work, but they do not own canon, credentials, merge authority, deployment authority, or physical-device authority.

## What makes Skeleton different

- **Human authority is explicit.** Security-sensitive actions stay behind maintainer approval and exact-state checks.
- **Execution is bounded.** Registered runners and executors replace ad-hoc shell authority.
- **Exact state matters.** Workflows bind to repository, branch/head SHA, declared files, target project, and operation metadata.
- **Verification is separate from execution.** Skeleton distinguishes a command being sent, accepted, applied, and independently verified.
- **Public and private state are separated.** Public repositories contain public-safe canon; secrets, private runtime state, credentials, personal data, and device topology stay outside them.
- **Model providers are interchangeable.** Codex, ChatGPT, local models, and other providers sit behind explicit routing and role boundaries.
- **Recovery is first-class.** Rollback, failure history, leases, retries, and the next safe action are part of the control model.
- **The system is exercised on real maintenance work.** Runner, Android/Home Edge, cross-repo routing, and the extracted Skeleton Media subsystem create a non-trivial engineering and security surface.

## Proven building blocks

The repository already contains executable or tested implementations of:

- manifest-driven boot through BOOT_MANIFEST.yaml and core/boot_loader.py;
- project/repository routing through PROJECT_INDEX.yaml and PROJECT_TREE.yaml;
- an approval-oriented ActionGate bound to exact PR metadata and reviewed head SHA;
- a public-safe append-only audit ledger that rejects obvious secrets, private paths, Drive references, and raw environment content;
- isolated Runner issue workspaces and allowlisted cross-repository routing;
- runtime-maintenance tasks separated from normal Codex issue execution;
- Home Edge execution contracts for real device/service actions;
- independent post-condition verification and fail-closed maintenance paths;
- a separate public media implementation in [alanua/skeleton-media](https://github.com/alanua/skeleton-media), with Skeleton retaining only the control-plane boundary.

See [Project Impact](docs/PROJECT_IMPACT.md) for maintainer and real-world usage evidence, [Threat Model](docs/THREAT_MODEL.md) for trust boundaries, and [Quickstart](docs/QUICKSTART.md) for a safe local proof.

## Why Skeleton exists

LLM-assisted systems become difficult to trust when context, permissions, execution, memory, and recovery live only in chat. Skeleton makes those concerns explicit and inspectable.

Core principles:

1. **Declared context** — boot from canonical manifests instead of reconstructing state from conversation history.
2. **Model neutrality** — keep ChatGPT, Codex, local models, runners, and other agents behind explicit role contracts.
3. **Read before write** — durable mutations require current state and the correct control path.
4. **Bounded execution** — actions flow through registered executors and approval/risk gates rather than ad-hoc shell access.
5. **Auditable outcomes** — distinguish sent, accepted, applied, and independently verified state.
6. **Durable continuity** — route semantic memory, registries, live state, and audit evidence to the right stores.
7. **Recoverability** — preserve rollback, failure history, and the next safe action.

## Five-minute local proof

This path is intentionally read-only and does not need credentials, cloud services, or Home Edge access.

    git clone https://github.com/alanua/Skeleton.git
    cd Skeleton
    python -m venv .venv
    . .venv/bin/activate
    python -m pip install --upgrade pip
    python -m pip install PyYAML pytest

    python -m core.boot_loader
    python -m pytest -q \
      tests/test_boot_loader.py \
      tests/test_action_gate.py \
      tests/test_audit_ledger.py

The boot report declares writes: none; the focused tests exercise boot validation, exact approval gating, and public-safe audit handling.

## Repository identity

    alanua/Skeleton        = Skeleton Core control plane
    alanua/skeleton-media  = public media subsystem for Home Edge
    alanua/bauclock        = public construction time/compliance application

Historical prototypes may be referenced as evidence, but they are not canonical runtime authority.

## Current status

    Status: active development
    Canonical boot: BOOT_MANIFEST.yaml
    Execution model: bounded / approval-oriented
    Public/private boundary: enforced by project policy and validation code

Interfaces and registries continue to evolve. Safety, explicit authority, auditability, and recoverability are treated as compatibility constraints rather than optional features.

## Start here

- [Quickstart](docs/QUICKSTART.md) — safe local proof with no external side effects
- [Project Impact](docs/PROJECT_IMPACT.md) — real maintenance and usage evidence
- [Threat Model](docs/THREAT_MODEL.md) — trust boundaries and security assumptions
- BOOT_MANIFEST.yaml — canonical boot/context entrypoint
- PROJECT_INDEX.yaml / PROJECT_TREE.yaml — project structure and execution routes
- CAPABILITY_REGISTRY.yaml — declared capabilities
- EXECUTOR_REGISTRY.yaml — registered execution paths
- PROVIDER_ROUTING.yaml — provider/model routing
- MEMORY_ROUTING.yaml — memory trust/privacy routing
- OPERATOR_RULES.yaml — operator and mutation rules
- [Action Gate](docs/ACTION_GATE.md) — approval-gating model
- [Audit Ledger](docs/AUDIT_LEDGER.md) — audit evidence model
- [AI-Assisted Maintenance](docs/AI_ASSISTED_MAINTENANCE.md) — how models are used without giving them final authority

## Maintainer workflow

Skeleton is maintained through issue triage, scoped branches/PRs, review, CI/runtime validation, exact-head verification, and bounded deployment/activation steps. AI tools assist with research, implementation, tests, review, and handoff synthesis, but merges and security-sensitive actions remain maintainer-controlled.

The workflow deliberately follows an authority ladder:

    Human decision / repository rules
            >
    Canonical private runtime state
            >
    Derived indexes and summaries
            >
    LLM inference

Derived state or model output may inform work, but it does not silently override higher-authority sources.

See:

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)
- [MAINTAINERS.md](MAINTAINERS.md)
- [docs/AI_ASSISTED_MAINTENANCE.md](docs/AI_ASSISTED_MAINTENANCE.md)

## Security model

Skeleton coordinates code, runtimes, infrastructure, and physical-device control paths. It therefore treats executor boundaries, approval gates, cross-repository authority, dependency provenance, secret handling, rollback, and post-condition verification as security concerns.

Normal code changes are separated from actions that can affect credentials, infrastructure, physical devices, networks, firmware, or user data. Such operations require the registered control path and the appropriate approval boundary.

Do not publish secrets, private device topology, credentials, tokens, private keys, or sensitive runtime state in issues or pull requests. See [SECURITY.md](SECURITY.md) and [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Core rule

    Manifest controls behavior.
    Markdown explains behavior.
    Tooling verifies behavior.
    Tests preserve behavior.

## License

Licensed under the [Apache License 2.0](LICENSE).
