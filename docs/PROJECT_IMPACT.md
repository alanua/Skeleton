# Project impact and real-world use

Skeleton is an early-stage open-source project with active primary-maintainer use. It should not be evaluated as a large-adoption project today; its current significance comes from the breadth and risk level of the engineering boundary it is already being used to control.

## Current use

The primary maintainer uses Skeleton to coordinate and verify engineering work across multiple connected public repositories and a live Home Edge environment.

Examples include:

- project boot and cross-repository routing through `PROJECT_INDEX.yaml` and `PROJECT_TREE.yaml`;
- bounded Runner tasks with explicit execution and approval contracts;
- GitHub issue/PR workflows that separate code-generation work from protected merge and runtime activation;
- Home Edge operations where command delivery is not treated as success until the resulting service/device state is checked independently;
- recovery-oriented maintenance with exact source revisions, rollback paths, idempotency and audit evidence;
- public/private separation of runtime state, credentials, private topology and personal data.

## Concrete public evidence

### Skeleton Media extraction

The media subsystem was separated into the independent Apache-2.0 repository [alanua/skeleton-media](https://github.com/alanua/skeleton-media).

The split was performed incrementally rather than as a copy-and-forget fork:

- PR #4184 registered Skeleton Media as a canonical separate project;
- PR #4185 introduced an explicit cross-repository bridge;
- PR #4186 removed duplicated media implementation from Skeleton Core and made the external boundary fail closed.

The result demonstrates one of Skeleton's intended jobs: preserve a controlled boundary while a live subsystem moves between repositories and runtime ownership domains.

### Home Edge and physical-state verification

Skeleton includes execution contracts for a Home Edge node used by the maintainer to control services and integrations connected to real devices.

The design deliberately separates:

1. request sent;
2. command accepted;
3. mutation applied;
4. real or independently observable postcondition verified.

This distinction is especially important for edge and device workflows where an exit code or HTTP 200 does not prove that the requested physical/application state actually happened.

### AI-assisted maintenance

The repository documents human-controlled AI-assisted engineering in `docs/AI_ASSISTED_MAINTENANCE.md`.

AI tools are used for bounded tasks such as investigation, patch generation, regression tests, issue triage, review assistance, and recovery planning. Merge, deployment, credentials, firmware, destructive operations and other security-sensitive actions remain under maintainer control.

## Representative fail-closed maintenance incident

A useful public example is the Runner recovery chain around issues [#3591](https://github.com/alanua/Skeleton/issues/3591), [#3593](https://github.com/alanua/Skeleton/issues/3593), and [#3594](https://github.com/alanua/Skeleton/issues/3594).

That incident is historical evidence, not a claim about the current Runner state. It shows how the project handles a serious execution-path failure:

- preserved worktrees were treated as recoverable state rather than disposable scratch data;
- a PermissionError was given a stable blocker signature instead of being retried blindly;
- diagnostic work was constrained to a narrow allowlist and public-safe evidence schema;
- false terminal/DONE states were explicitly treated as a correctness bug;
- the foundation moratorium defined measurable exit gates, including crash/restart survival and durable publication evidence;
- protected changes, deployment and runtime mutation remained separately operator-gated.

More recent P0 work follows the same pattern. For example, issue [#4172](https://github.com/alanua/Skeleton/issues/4172) defines a primary-Codex-only health probe that must bypass fallback providers, make at most one provider request, use a read-only sandbox, avoid temporary git/codegen state, and fail closed on missing executable/auth/route evidence before retrying [#4170](https://github.com/alanua/Skeleton/issues/4170).

The value of these examples is not that the system never fails. It is that failures are made observable, bounded, recoverable and reviewable instead of being hidden behind an agent's apparent success.

## Why the project can matter beyond one installation

The reusable problem is not "home automation" or one private deployment. It is the authority boundary between an AI/coding agent and systems that can actually change state.

Skeleton's reusable abstractions include:

- declared context and project routing;
- model/provider-neutral capability contracts;
- approval and mutation gates;
- registered executors;
- fail-closed cross-repository boundaries;
- audit receipts;
- post-condition verification;
- rollback and retry metadata;
- separation of public source from private runtime authority.

These patterns can apply to software maintenance, CI/CD, internal tools, edge systems and agent-driven operations without coupling the project to one model provider.

## Adoption statement

Skeleton does not claim broad external adoption, large download counts or a mature downstream ecosystem today.

The accurate current claim is:

> Active primary-maintainer OSS use across several connected repositories and a real edge/runtime environment, with a growing public architecture intended to be reusable outside that environment.

This document should be updated as external contributors, users, releases or downstream integrations become verifiable.
