# Production Engineering Quality Pipeline

Phase 1 separates caller claims from derived proof.

`core.task_quality_gate` validates the supported claim fields without accepting
architecture, production, observed-diff, or runtime proof from caller-shaped data.
Allowed paths may be exact repository-relative paths or bounded `directory/**`
scopes. Absolute paths, traversal, bare `**`, mid-segment wildcards, empty scope
prefixes, unsafe wildcard forms, and control characters fail closed.

`core.quality_evidence` derives public-safe Phase 1 evidence from validated
claims. The protected surface is canonical and includes manifests, gate code,
workflow/deploy/runtime-sensitive directories, `INVARIANTS.yaml`, and
`core/architecture_invariants.py`. Composite private/public-safe privacy is
classified as protected while public evidence redacts raw private portions.

Phase 1 does not emit `ObservedDiffImpact`, touched-file reality, runtime proof,
or architecture readiness. Those remain later-phase requirements.
