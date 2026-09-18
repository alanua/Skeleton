# Project impact and real-world use

Skeleton is an early-stage public open-source project with real maintainer-operated production use.

It is not presented as a high-adoption library, and this document does not claim third-party deployments or download numbers that the project cannot substantiate. Its current impact is different: Skeleton is the control plane used to structure, review, execute, verify, and recover AI-assisted engineering work across multiple repositories and a real Home Edge environment.

## What Skeleton coordinates today

Skeleton provides reusable control-plane patterns for:

- project and repository routing;
- bounded Runner and runtime-maintenance work;
- explicit approval and execution lanes;
- audit receipts and independent post-condition checks;
- persistent project/context handoff;
- cross-repository contracts;
- local/edge execution where a code change can affect a real service or physical device;
- rollback-oriented maintenance.

Companion public repositories include:

- `alanua/skeleton-media` — media-domain implementation extracted from Skeleton Core;
- `alanua/bauclock` — a separate application project maintained with the same engineering discipline.

The repository also contains other public project routing entries and integration contracts, but public adoption remains early.

## Why this matters

Coding agents are increasingly capable of changing source code, CI, services, and deployment state. The difficult part is no longer only code generation. It is preserving human authority and making the path from intent to real-world effect inspectable.

Skeleton focuses on that boundary:

```text
intent
  -> declared project context
  -> bounded task
  -> approval / risk gate
  -> registered executor
  -> applied change
  -> independent verification
  -> durable audit / recovery state
```

The same model is useful whether the worker is Codex, another hosted model, a local model, or a human-operated tool.

## Evidence in the repository

Useful starting points:

- `BOOT_MANIFEST.yaml` — declared control-plane boot/context
- `PROJECT_INDEX.yaml` and `PROJECT_TREE.yaml` — project routing
- `EXECUTOR_REGISTRY.yaml` — registered execution paths
- `MEMORY_ROUTING.yaml` — memory/privacy routing
- `docs/ACTION_GATE.md` — mutation/approval model
- `docs/AUDIT_LEDGER.md` — evidence model
- `docs/HOME_EDGE_EXECUTOR.md` — bounded edge execution
- `docs/AI_ASSISTED_MAINTENANCE.md` — human-controlled AI maintenance
- `docs/SKELETON_MEDIA_EXTRACTION.md` — a concrete cross-repository separation/cutover example
- `docs/THREAT_MODEL.md` — security boundaries and abuse cases

## Maintainer workload

The primary maintainer uses Skeleton in day-to-day engineering rather than as a demonstration repository. Maintenance includes issue triage, scoped implementation, regression testing, pull-request review, exact-head validation, cross-repository contract work, and controlled runtime changes.

This is also the main reason additional Codex/API capacity would be useful: the bottleneck is sustained review, testing, security hardening, documentation, and maintenance across several connected repositories.

## Public/private boundary

The public repository intentionally excludes credentials, private keys, personal documents, private device registries, production databases, signing material, and private household topology.

Public source describes the reusable control plane. Private runtime state stays behind explicit trust boundaries.

## Issue queue semantics

Skeleton's GitHub issue count is not intended to represent an end-user bug count. The repository also uses issues as machine-readable, bounded engineering work records for Runner tasks, validation gates, recovery work, cross-repository maintenance, and operator-reviewed follow-up. Open issues can therefore include queued or blocked maintenance work alongside conventional bugs and feature requests.

That model is deliberate: work is kept inspectable in public-safe issue/PR history instead of being hidden inside an autonomous agent session. It also means raw issue count should not be interpreted as a measure of unresolved user-facing defects.

## Companion ecosystem and maturity

Skeleton coordinates several repositories with different maturity and licensing states. They should not all be described as equivalent open-source products:

- `alanua/skeleton-media` — separate Apache-2.0 public media subsystem extracted from Skeleton Core.
- `alanua/bauclock` — separate Apache-2.0 construction time-tracking and compliance-support application maintained with Skeleton-style bounded engineering workflows.
- `alanua/Lavalamp` — public WLED/ESP32 cylinder-firmware companion used through Skeleton physical-device controls. Its repository currently has no selected license, so Skeleton does not describe it as an open-source release.
- `alanua/Travel` — public-safe Travel domain architecture and reusable contracts. Its current public status remains bootstrap-level: live adapters are not implemented, private runtime is not connected, booking authority is none, and no repository license is currently selected.
- `alanua/DIOS` — public-safe PRE-ALPHA drawing/CAD domain. A separate DIOS video-analysis runtime may reuse Skeleton execution and audit facilities, but DIOS is not Skeleton Core and its repository currently states that no open-source license has been selected.

This distinction is part of the trust model: public source, open-source licensing, production maturity, and private runtime authority are separate claims and are documented separately.
