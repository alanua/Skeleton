# Universal Video Understanding — protected source-only Gate registration (#4788)

Status: **DRAFT / SOURCE_ONLY / NOT_REGISTERED_IN_RUNTIME / NOT_DEPLOYED**.
This document does not authorize merge, Gate activation, runner pickup, Home Edge/DE-PC operations,
MCP tool changes, secrets or media ingestion.

## Current exact source authority (verified 2026-10-20)

- Core base: `alanua/Skeleton` `b11c18a3dba5b1b797142002055bb867c41d3b0f` at branch creation; recheck before merge.
- Current external production source main (NOT installed runtime): `alanua/skeleton-media`
  `e234c86469467dd2131a6bf5e0c1250ccef1d686` (PRs #66, #69, #70 merged).
- Additional typed plan-only media operation `video_transcribe_url` is proposed in external **Draft PR #71**,
  exact head `638de9059138ab800b6d6ad166df24be2c1ba825`, **not merged**.
- Core's active legacy external pin remains `7089b1ba37292aab0283cadb8ac85644cc3d28f8`.
  **Do not update the pin or add an import allowlist entry now.** The external PR and exact
  package provenance must be reviewed and merged separately.

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

1. Review/merge external media Draft #71 independently with exact-head
   tests and strict operation-plan privacy check.
2. Re-audit latest Skeleton Core and external media SHAs; review exact
   source/package pin update and typed global provider ingress diffs.
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
