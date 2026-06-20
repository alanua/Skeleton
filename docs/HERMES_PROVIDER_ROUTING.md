# Hermes Provider Routing Policy v0

## Purpose

This policy defines a public-safe provider routing model for future Hermes work.
It is a planning and review contract only. It does not enable live provider calls.
It does not install provider SDKs, configure API keys, change runtime routing,
or grant approval to send data to any external model.

All provider names in this document are policy examples. They are not enabled live routes.

## Routing Model

Hermes work should be routed by task shape, data sensitivity, and cost risk.

| Role | Policy example | Intended use | Live route |
| --- | --- | --- | --- |
| `planner` | GPT/Codex class model | Short planning, decomposition, final decision framing, and operator-facing summaries. | Disabled |
| `bulk_worker` | Low-cost model such as DeepSeek through OpenRouter | High-volume public-safe extraction, chunk summarization, normalization, and draft comparison work. | Disabled |
| `critic` | Gemini or comparable auditor | Contradiction checks, scale/calibration review, QA notes, and challenge of unsupported conclusions. | Disabled |

The planner should keep prompts short and decide what can be safely delegated.
The bulk worker should receive only bounded, public-safe, sanitized chunks. The
critic should receive only sanitized summaries, aggregate calibration notes, and
claimed conclusions that need audit.

## Public Task Router

`core/hermes_task_router.py` is a public-safe static router. It classifies
synthetic or sanitized subtasks as `LOW`, `MID`, or `HIGH` and emits only one
of these public route aliases:

| Class | Alias | Intended public-safe task shape |
| --- | --- | --- |
| `LOW` | `AUFMASS_WORKER_LOW` | Deterministic extraction, normalization, repetitive calculations, and candidate generation. |
| `MID` | `AUFMASS_REVIEW_MID` | Geometry/evidence review, contradiction checks, tolerance decisions, and rework instructions. |
| `HIGH` | `AUFMASS_EXPERT_HIGH` | Unresolved high-impact ambiguity, method approval or rejection, and final expert adjudication. |

The router considers task type, ambiguity, impact, evidence quality, privacy
class, retry count, and operator gate state. The emitted task packet includes
route metadata for budget, retry, evidence, privacy, and operator approval.
provider/model names stay out of public task packets; aliases are the only
route identifiers.

Supported transitions are explicit:

- `LOW->MID` when a low-cost task needs review because ambiguity, impact,
  retry, or evidence conditions exceed low-risk handling.
- `MID->LOW_REWORK` when review produces bounded rework instructions that can
  be handled by a low-cost worker later.
- `MID->HIGH` when review exposes unresolved high-impact ambiguity or a method
  approval/rejection decision.
- `HIGH->REQUEST_EVIDENCE` when expert handling cannot proceed because
  evidence is missing, insufficient, or cannot cross the public-safe boundary.

Never silently downgrade HIGH to a cheaper route. If a caller requests
`AUFMASS_WORKER_LOW` or `AUFMASS_REVIEW_MID` for a task classified as `HIGH`,
the router must fail closed instead of substituting a cheaper alias. Fail closed
also applies when any requested alias is unavailable. These failures are local
validation errors only; they do not call providers or change runtime services.

## Troitsa-inspired pattern

The policy borrows the general shape of a three-role pattern: one model plans,
one low-cost model does high-volume execution, and one independent model audits
the result. Skeleton does not import, vendor, execute, or depend on any external
repository, script, prompt pack, or routing implementation for this pattern.

## Budget Gates

Provider routing remains blocked unless every budget gate is satisfied:

- A per-run budget must be declared before any live provider use.
- A token cap must be declared for the whole run and for each routed chunk.
- A maximum retry count must be declared before work starts.
- Manual approval is required before any live provider use.
- Work must stop on unknown cost, unknown quota, missing quota visibility, or
  provider billing ambiguity.
- Retries must stop when the retry cap is reached, even if the task is
  incomplete.
- A route must prefer local/private Hermes handling when data sensitivity is
  higher than the approved provider route.

## Privacy Gates

No external provider route may receive private material unless an explicit
private-provider approval route exists for the exact data and provider. Without
that approval, prompts and attachments must contain none of the following:

- Real drawings, plan screenshots, DXF/PDF/image artifacts, or raw excerpts.
- Google Drive links, Google document links, Drive file IDs, or folder IDs.
- Real quantities, measurements, room tables, estimates, prices, or takeoff
  results.
- Customer names, addresses, project identifiers, contact details, or operator
  notes that reveal private context.
- Secrets, credentials, tokens, keys, cookies, environment values, or runtime
  configuration values.
- Local paths, private workspace paths, repository-adjacent private paths, or
  private task packets.

Sanitization must remove or replace private identifiers before any approved
external route. Redaction is not sufficient if the remaining context can still
identify a customer, project, drawing, Drive file, or private quantity.

## Aufmass Routing

Aufmass work has a stricter boundary because raw artifacts and quantities are
private by default.

- Private local/Hermes handling owns Google Drive, Sheets, raw drawings, raw
  extraction artifacts, and private workbook outputs.
- The planner may prepare public-safe task shapes and decide whether sanitized
  delegation is worth requesting.
- The cheap worker may only receive sanitized chunks after approval. Sanitized
  chunks must exclude drawings, Drive references, customer data, real quantities,
  raw tables, paths, and private task packets.
- The critic may only receive sanitized aggregate notes, calibration summaries,
  contradiction claims, and QA questions after approval. It must not receive raw
  drawings, exact private measurements, or private project identifiers.
- If there is any uncertainty about whether an Aufmass prompt is sanitized, the
  route stays private/local and the external provider route is blocked.

## Failure Modes

Provider routing must explicitly account for these failure modes:

- Model drift changing extraction behavior or calibration assumptions.
- Hallucinated dimensions, quantities, rooms, openings, or source references.
- Private leakage through prompts, attachments, logs, traces, retries, or QA
  summaries.
- Provider outage, degraded latency, API errors, or unavailable model versions.
- Quota exhaustion, billing ambiguity, or missing cost telemetry.
- Inconsistent calibration between planner, worker, critic, and local Hermes
  results.
- Excessive retries that increase cost or leak more context without improving
  confidence.

## Approval Boundary

This policy is not an approval. A future live route must define the exact
provider, data class, budget, token cap, retry cap, logging behavior, approval
record, rollback behavior, and stop conditions before any external call is made.
Provider routing that cannot be described in public-safe terms must remain
private and unimplemented in the public repository.
