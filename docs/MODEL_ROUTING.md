# Model routing

Skeleton treats concrete model names as registry data, never as task authority.

## Candidate discovery

External sources are useful for finding challengers, not for granting authority. OpenRouter catalog/rankings, official provider metadata, and external benchmark leaderboards may contribute public-safe discovery signals. Before spending canary budget, Skeleton applies advertised-fit filters such as required capability/tool support, context size, provider/policy availability, privacy compatibility, and bounded cost. Among candidates that pass those filters, external score, cost, and latency may deterministically order the canary shortlist.

External evidence cannot mark a capability ELIGIBLE or LIVE.

## Promotion lifecycle

Promotion is capability-specific and follows:

`DISCOVERED -> CANARY_ONLY -> ELIGIBLE -> LIVE`

- `DISCOVERED`: external metadata/ranking/benchmark evidence only.
- `CANARY_ONLY`: explicitly admitted to a bounded Skeleton-owned canary after policy/privacy checks.
- `ELIGIBLE`: an exact Skeleton canary passed for that capability with no hard failure. This is evidence that the candidate may be considered by later routing integration; it is not production authority.
- `LIVE`: a separately activated production route after the later Execution Fabric policy/gate step. Canary PASS alone does not produce LIVE.

Negative/operational states include `DEGRADED`, `UNSUPPORTED`, and `BLOCKED`.

Hard failures such as missing deliverables, tool-use failure, scope violation, privacy violation, or validation failure are exclusion evidence for the affected capability and are not averaged away by external scores or unrelated successes.

## Task-fit ranking

Evaluation ranking is deterministic: required capability quality and policy approval first, privacy as a hard gate, then locality preference, verified health, latency, cost, and stable model id as the final tie-breaker. A weak local model is never selected for a hard task merely because it is cheaper or local.

Production ranking must additionally request `production_only`, which requires every needed capability to have explicit `LIVE` promotion. An `ELIGIBLE` challenger may win evaluation/canary ranking while remaining impossible to select for production.

`MODEL_REGISTRY.yaml` is intentionally a public-safe measured roster. Private prompts and raw runtime evidence remain local; only sanitized aggregate/canary evidence may be promoted into reviewed registry data.

The current Kimi evidence is therefore an ELIGIBLE challenger for the tested OpenHands capability, not a universal default and not automatically LIVE. The legacy GLM response canary does not overcome its repository-deliverable/tool-use hard failures.

This slice does not change production routing. Runner's transitional fallback remains separate until Execution Fabric protected integration passes exact-head review and live canaries.

## Provider-neutral routing core

The provider routing core is implemented as a static, provider-neutral decision layer in
`core/provider_router.py`, `core/provider_policy.py`, `core/provider_registry.py`, and
`core/provider_telemetry.py`. It does not call Codex, Ollama, OpenRouter, Gemini, Claude,
Kimi, or any other live provider. Registry names are public-safe aliases only.

Supported route classes are:

- `DETERMINISTIC`: no LLM, no provider alias, no model alias.
- `LOCAL_LOW_COST`: local/private-capable low-cost route for easy work only when capability is sufficient.
- `STRONG_CODING`: primary strong coding route alias.
- `SECONDARY_CODING`: bounded fallback strong coding route alias.
- `CLOUD_STRONG_REVIEW`: strong review route alias.

Eligibility is a hard-gated decision over typed task class, required capability, privacy
class, recent provider health, bounded cost class, and bounded latency class. Task text is
untrusted context and cannot create providers, models, endpoints, or authority. Unknown
provider or model names in task text are ignored by the registry and never appear in
route receipts.

Deterministic tasks select `DETERMINISTIC` and produce `NO_LLM_REQUIRED`. Hard coding and
strong review tasks require strong routes; they cannot silently downgrade to
`LOCAL_LOW_COST` solely because local is healthy, cheaper, or preferred. Provider outages
remove only the affected provider alias from the eligible set. If no bounded strong route
remains, the decision is `NEEDS_OPERATOR` with `NO_ELIGIBLE_BOUNDED_ROUTE`, not an
unbounded retry loop.

Privacy is also a hard gate. `LOCAL_PRIVATE` requests can select only local routes and
cannot select a disallowed cloud route. Public and sanitized requests may select cloud
aliases only when capability, health, cost, and latency gates pass.

Route receipts are intentionally public-safe. They contain only status, route class,
provider/model aliases, reason code, considered route classes, and aggregate latency/cost
classes. They do not include prompts, private payloads, endpoints, secrets, raw provider
errors, or task text.
