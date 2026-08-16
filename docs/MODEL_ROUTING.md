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
