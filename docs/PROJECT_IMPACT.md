# Project Impact and Real-World Use

Skeleton is an early-adoption open-source project with a large real maintenance surface. This page documents verifiable use and maintainer responsibility without claiming adoption metrics that the repository cannot substantiate.

## Maintainer responsibility

The primary maintainer is responsible for:

- issue triage and task scoping;
- pull-request review and exact-head merge approval;
- CI and regression validation;
- release/runtime readiness;
- cross-repository routing and compatibility;
- rollback and recovery paths;
- Home Edge and Android integration work;
- security boundaries around credentials, private runtime state, and physical-device operations.

AI tools assist with implementation and review, but they do not own merge, deployment, credential, firmware, network, or physical-device authority.

## Real system use

Skeleton is used as the control plane for day-to-day engineering rather than as a standalone demonstration.

Current public-facing evidence includes:

- **Runner / issue workspaces** — normal bounded tasks are isolated into issue-specific workspaces instead of mutating the coordinator checkout.
- **ActionGate** — repository actions can be bound to an exact PR number, expected head SHA, expected files, allowlisted repository, and explicit user approval.
- **AuditLedger** — operational events are append-only and validated for public safety before persistence.
- **Cross-repository routing** — public projects are registered explicitly in PROJECT_TREE.yaml rather than discovered or inferred ad hoc.
- **Home Edge contracts** — physical/service execution is treated as a distinct, approval- and verification-sensitive boundary.
- **Skeleton Media extraction** — media implementation was split from Skeleton Core into [alanua/skeleton-media](https://github.com/alanua/skeleton-media), leaving Skeleton as the control plane.

## Recent public maintenance evidence

The following merged changes demonstrate active architectural maintenance:

- [#4183](https://github.com/alanua/Skeleton/pull/4183) — OSS-readiness/community-health work and Apache-2.0 licensing.
- [#4184](https://github.com/alanua/Skeleton/pull/4184) — registration of the extracted Skeleton Media repository as a separate project.
- [#4185](https://github.com/alanua/Skeleton/pull/4185) — explicit Skeleton-to-Skeleton-Media bridge with fail-closed behavior.
- [#4186](https://github.com/alanua/Skeleton/pull/4186) — removal of duplicate media implementation from Skeleton Core, completing source separation.

These are not synthetic showcase changes; they are part of an ongoing refactor of a running system toward clearer repository ownership and safer operational boundaries.

## Why ecosystem importance can exist before broad adoption

Skeleton addresses a reusable problem that appears whenever AI coding agents gain real execution authority:

> How can a maintainer let models help across repositories, services, private state, and edge devices without making model output the root of trust?

The project explores a provider-neutral answer built around explicit authority, bounded execution, audit evidence, rollback, and independent verification. Those mechanisms are reusable beyond the maintainer's own environment even while public adoption remains early.

## Companion projects

- [alanua/skeleton-media](https://github.com/alanua/skeleton-media) — media-domain implementation extracted behind an explicit cross-repo contract.
- [alanua/bauclock](https://github.com/alanua/bauclock) — a separate public application with its own domain boundaries and maintenance lifecycle.

The purpose of keeping these repositories separate is to avoid turning Skeleton into a monolith. Skeleton owns control-plane concerns; domain projects own their implementation.

## What this page does not claim

This repository does **not** claim:

- a star/download count as a proxy for impact;
- broad external production adoption without evidence;
- formal verification of the entire system;
- that AI-generated output is trusted without human review or test evidence.

The strongest current evidence is active maintenance of a real, multi-repository, security-sensitive engineering system and the reusable control patterns that result from that work.
