# Execution Fabric — model evidence slice

This document records the non-protected measured-model layer used by Execution Fabric.

Executors and models remain separate authority dimensions. The model roster answers only whether a registered model is sufficiently measured for a required capability class. It does not grant repository, merge, deploy, device, finance, legal, or governance authority.

A future `ExecutionBinding` may reference one model record only after executor compatibility, task capability, privacy, policy, credential, health, and budget gates all pass together. This slice does not create or dispatch bindings and does not modify the live Runner route.

Measured capability records use per-capability `LIVE | DEGRADED | COOLDOWN | DISABLED | UNSUPPORTED` state. `LIVE` requires a Skeleton bounded canary. Response-only success does not imply tool-use or repository-edit eligibility; a required artifact missing is hard failure evidence.

The deterministic selector consumes a code-owned `TaskFitRequest` containing required capability thresholds and privacy class. It has no field for caller-provided provider/model/endpoint authority. Given the same request and registry snapshot it returns the same ranking.

Next phase under #2809 integrates this measured roster into atomic executor+model ExecutionBindings and immutable RouteLease dispatch. Any protected Runner integration requires exact-head operator approval.
