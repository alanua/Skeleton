# Model routing

Skeleton treats concrete model names as registry data, never as task authority.

Eligibility is capability-specific. A model can be LIVE for reasoning and UNSUPPORTED for repository edits. `LIVE` requires a Skeleton-owned canary PASS for that capability snapshot; external leaderboards or provider metadata can shortlist candidates but cannot promote them to LIVE.

Hard failures such as missing deliverables, tool-use failure, scope violation, privacy violation, or validation failure are exclusion evidence for the affected capability and are not averaged away by external scores.

Selection order is deterministic: required capability quality and policy approval first, privacy as a hard gate, then locality preference, verified health, latency, cost, and stable model id as the final tie-breaker. A weak local model is never selected for a hard task merely because it is cheaper or local.

`MODEL_REGISTRY.yaml` is intentionally a public-safe measured roster. Private runtime evidence remains local and only sanitized aggregate/canary status may be promoted into this file through reviewed changes.

This slice does not change production routing. The Runner's transitional fallback remains separate until Execution Fabric protected integration passes exact-head review and live canaries.
