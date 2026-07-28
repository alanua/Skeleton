# Preliminary findings from repository and control-plane evidence

These are confirmed repository/control-plane findings, not a substitute for live inspection.

## P0 — no guaranteed live audit channel in the current assistant context

The canonical Home Edge paths and `skeleton-devices` registry CLI are not mounted here, and no connected `Skeleton Home Edge` tool is exposed. A normal operator-facing audit should not depend on whichever chat runtime happens to contain the connector. Skeleton needs one stable, public-safe, read-only `home_edge_full_audit` maintenance operation that can be invoked through the canonical executor and returns a bounded aggregate receipt.

## P0 — code, deployment and physical verification are not consistently the same state

Several work streams demonstrate that a merged PR or closed issue can exist while the live function remains incomplete:

- the adaptive universal remote was merged as an offline reference, while the served `/remote` route remained a separate integration problem;
- the document pipeline has repository implementations but current work still includes activation, source selection, watchdog and stuck-outbox repair;
- Video Understanding has merged runtime components but remains blocked by canonical-memory-root resolution;
- Scheduler launched on Hetzner, while user-level release boundary and rollback corrections remain open.

The audit must classify every function independently as source-ready, installed, enabled, running, applied and physically verified.

## P0 — autonomous MFP/document pipeline is not finished

Open work shows all of these still require verification or completion:

- autonomous document intake PR is draft and its installer explicitly does not enable or start the service;
- Brother automatic ADF-to-flatbed selection has an open restoration task;
- Brother scan-key watchdog installation has an open task;
- document inference outbox has a stuck-diagnostic task;
- scan-session/duplex assembly and canonical MemoryGateway/Drive projection still have unresolved work.

Until a real scan from the MFP panel is automatically split, assembled, OCR-processed, archived, projected and verified after restart without chat intervention, this subsystem is not autonomous.

## P0 — memory authority and projection lifecycle are still fragmented

Open audit/migration work and stale protected PRs show unresolved control-boundary questions around:

- MemoryGateway as the sole normal mutation/read-control boundary;
- canonical root resolution;
- durable projection outbox and replay recovery;
- mandatory project-scoped bootstrap;
- Cognee/MemPalace/Graphify freshness and handoff;
- stale superseded branches and PRs that can misrepresent current authority.

No Home Edge process should invent or create a second canonical database. The live audit must prove the exact canonical authority, integrity, backup, mutation route and projection recovery.

## P1 — no evidence of one complete autonomy supervisor

Feature-specific services and watchdogs exist, but repository evidence does not prove one canonical supervisor that continuously checks:

- required service/timer enabled and active state;
- duplicate workers;
- queue age, retries and quarantine growth;
- registry/state freshness;
- API health and physical postconditions;
- runtime drift from approved release hashes;
- backup and rollback readiness;
- latency regressions;
- alerting only after bounded self-repair fails.

The target is not one unrestricted repair daemon. It is one read-only health aggregator plus allowlisted, idempotent, per-operation recovery policies.

## P1 — network administration remains unfinished

The ASUS gateway, Huawei modem and downstream MikroTik roles are known, but the secret-safe administration/optimization route remains blocked/incomplete. Modem active-state validation also has unresolved work. This audit is read-only: it must inventory topology, configuration ownership, monitoring, link recovery, DNS/DHCP/firewall drift and backup readiness without changing router/modem settings.

## P1 — media controls are not fully productized

The universal remote/gamepad remains under external diagnosis. Media PC/USB input practical smoke and some older diagnostic tasks remain unresolved. The audit must prove that routine mode selection, volume, navigation, application launch, playback and safe exit use registered reusable operations, preserve current mode where required and verify the real foreground/application state.

## P1 — Scheduler is not yet a universal Home Edge automation layer

Scheduler Core provides durable schedules, idempotency, misfire/overlap handling and restart recovery, but current open correction work shows runtime release/rollback and user-level activation boundaries are not final. The audit must determine which schedules actually execute on Home Edge, which only propose actions, and whether every recurring home process is represented by a durable registered schedule or event-driven service.

## P1 — device/operation registry completeness is unknown

The live registry was not readable in this context. The audit must find:

- identity-pending devices;
- devices identified only by IP;
- capabilities without reusable operations;
- operations lacking exact preconditions, independent verification, retry or rollback;
- stale paths and expired states;
- operations with no recent success or only `sent/accepted` evidence;
- real devices not represented in the canonical registry.

## P2 — issue/PR hygiene is itself an operational risk

Multiple open draft PRs are explicitly superseded, diverged or blocked, while newer implementations have merged. The audit must map live runtime hashes to current `main`, mark stale work as superseded, and ensure no automation can deploy from an obsolete branch merely because it remains open.

## Required outcome

The live audit must produce a machine-readable inventory and a prioritized repair queue. The first repair phase should address only green/read-only or clearly idempotent safe fixes. Network mutations, credentials, pairing, firmware, package installation, reboot, destructive cleanup and factory reset require separate explicit approval.
