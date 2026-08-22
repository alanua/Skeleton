# Production Engineering Quality Pipeline

Phase 1 normalizes Runner task quality claims before any later evaluator or
runtime proof layer is allowed to interpret them.

The current implementation is intentionally claim-side only:

- privacy boundaries normalize public-safe, private, protected-private, and
  composite private/public-safe Runner inputs without exposing private values in
  public mappings;
- allowed scopes are bounded repository-relative exact paths or anchored
  `path/**` globs only;
- idempotency keys must be stable, bounded, and contain the repository identity,
  including validation keys such as
  `validate-pr-branch:alanua/Skeleton:pr-3181:...`;
- normalized risk and explicit `protected_intent` are part of `TaskSpec`;
- declared protected paths and globs are classified deterministically, and any
  protected signal requires protected review.

PUBLIC_SAFE privacy, green/low risk, or caller-supplied reviewer strings cannot
downgrade stronger protected signals. Protected review remains required for
private/composite privacy boundaries, explicit protected intent, protected
declared scopes such as `.github/workflows/**`,
`scripts/runner_poll_github_tasks.py`, or `core/gate_engine.py`, high or
critical risk, configured protected risk, invalidated head-bound evidence, and
required missing architecture or production-contract review receipts.

Phase 1 does not synthesize touched files, `ObservedDiffImpact`, architecture
evaluator results, evidence-authenticity elevation, runtime proof, deployment
proof, or merge authority. No input may produce `RUNTIME_PROVEN` in this phase;
runtime-style evidence is normalized back to head-bound claim evidence and is
invalidated when the repository head changes.
