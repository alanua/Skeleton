# Universal Video Understanding — protected source-only Gate registration (#4788)

Status: **SOURCE_ONLY / NOT_REGISTERED_IN_RUNTIME / NOT_DEPLOYED**.
This document does not authorize merge, Gate activation, runner pickup, Home Edge/DE-PC operations,
MCP tool changes, secrets or media ingestion.

## Current exact source authority (reverified 2026-10-23; protected migration Draft)

- Core `alanua/Skeleton/main` at this protected migration branch creation: `cb2070a84ffd06358b84d789452a53db30921969`. Source-only protected PRs #4816 and #4818 are merged; recheck exact base before any new protected merge.
- External `alanua/skeleton-media/main`: `ef5c45f90b069d6d54c5e72b8b34a1f5cfc0fd07` (GitHub verified, **source-only**, not installed runtime). Media PRs #66, #69, #70, #71 and #72 are merged.
- Ninth typed plan-only `video_transcribe_url` operation was merged from media PR #71,
  approved exact head `638de9059138ab800b6d6ad166df24be2c1ba825`.
- Protected Core PR #4816 registered the inactive source-only plan; protected PR #4818 aligned the Core media pin, source candidate and CI workflow to the then-current media source. This **new protected Draft** proposes to align both bridge constants, CI checkout/assertion, tests and `video_understanding_global.external_source_sha` with `ef5c45f90b069d6d54c5e72b8b34a1f5cfc0fd07`. Neither source alignment nor green CI proves an installed or executable runtime package.
- Media `video_understanding.operations` remains **outside** `_SUPPORTED_MODULES`; SHA alignment does not authorize bridge imports, live execution, extra providers or connector/app publication.

## One provider, no second conveyor

`CAPABILITY_REGISTRY.yaml` declares **one** proposed source-only provider:
`skeleton.video_understanding.global`.
It lists the prior eight video semantic operations **and** the ninth typed
`video_transcribe_url`, in the same provider. This is a *static, protected
source declaration*, not evidence that a chat/ancillary can invoke it.
`status: stage1_dry_run`, `runtime_registered: false`,
`live_runtime_execution: false`, and `deployment_state: NOT_DEPLOYED`
are intentional.

Keep the existing Video Understanding worker/queue/MemoryGateway source of truth;
preserve the legacy DIOS queue and TV playback. Do not create a second worker,
chat-local executor, database, installed service, systemd timer, queue or
provider. Domain/ancillary origin changes interpretation and authorization,
not pipeline ownership. All authorized ancillary identities must resolve
to the same one provider id.

## Gate and privacy contract

`schemas/video_transcribe_ingress.schema.json` bounds caller-controlled
semantic input to `operation=video_transcribe_url`, an HTTPS public supported
video URL, `mode=QUICK|STANDARD`, and optional strict boolean
`verify_with_independent_asr`.

`core.skeleton_media_bridge.plan_global_video_transcribe_ingress` is a
**pure proposed preflight**, not an execution path. It requires an
`authenticated_caller` and `trusted_gate_check` injected **by a trusted
host outside the user payload**. It rejects missing/unapproved caller,
missing/false/error Gate decisions and any extra caller-provided fields
including fake approval, credentials, shell, model paths or output paths.
It does not execute media, transport private audio, call an LLM, write to
MemoryGateway, inspect the live workstation or register a tool. An injected
synthetic test Gate is **not** production security enforcement.

The private plan contains the URL and must remain on approved private
storage/transport. Public receipt contains only the provider id, operation,
not-registered status and non-execution markers; never URL, title, media,
transcript, model, caller identity, secrets or private path. Neither
`status=PLANNED_NOT_REGISTERED` nor green synthetic CI proves runtime
availability or transcription word accuracy.

Do not modify `core/gate_engine.py`, `core/action_gate.py`,
Runner files, `scripts/runner_poll_github_tasks.py` or the existing
two stable `Skeleton Control MCP` tools. The connector/app registration
boundary must be identified and explicitly operator-approved separately.

## Protected activation checklist — separate authority required

1. **SOURCE-ONLY DONE:** media PR #71 added the planned video transcription URL ingress and PR #72 merged subtitle cue-order/provenance safety. External media main is `ef5c45f90b069d6d54c5e72b8b34a1f5cfc0fd07`. Neither source merge is a runtime deployment.
2. Protected Core #4816 and #4818 are merged source-only. This protected SHA-alignment Draft must pass exact-head CI and obtain a distinct exact-head merge approval. Separately prove installed package parity before any Gate activation.
3. Resolve authenticated connector principal and host-native Gate
   decision source; deny forged/expired/replayed identities and requests.
   Validate the same provider from at least two synthetic ancillary
   identities without allocating additional queues or workers.
4. Plan one bounded Gate-authorized executor and private audio transport,
   offline/timeout cleanup and idempotency behavior; do not run from
   ChatGPT or arbitrary shell by default.
5. Complete isolated private fixture acceptance, independent ASR
   disagreement/word accuracy labeling and DIOS/TV before/after checks.
6. Obtain distinct operator exact-head approvals for protected merge,
   runtime sync, connection/tool publication and production deployment.
   Rollback authority and original DIOS queue must remain available.

Verdict until all gates are complete: **CONTRACT_ONLY / NEEDS_OPERATOR /
DO_NOT_DEPLOY / DO_NOT_ACTIVATE**.
