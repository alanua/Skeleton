# Production Engineering Quality Pipeline

Phase 1 normalizes Runner TaskSpec claims into a deterministic public policy
shape. It is intentionally lossless for the claim-side contract and intentionally
non-authoritative for proof.

The Phase 1 normalizer preserves:

- repository, base reference, optional exact base SHA, head branch/reference, and optional expected head SHA
- task kind and JSON-compatible payload
- requested capabilities
- declared allowed paths/scopes as `DECLARED_ONLY`
- forbidden actions, validation, expected output, privacy boundary, idempotency key, risk, and protected intent

Accepted current and legacy aliases are normalized only when all supplied aliases
agree. Disagreement fails closed.

Phase 1 does not accept caller-supplied evidence receipts as proof of
architecture, production contract, observed reality, review state, or runtime
state. `architecture_required=true` yields
`ARCHITECTURE_REVIEW_REQUIRED`; `production_contract_required=true` yields
`PRODUCTION_CONTRACT_REVIEW_REQUIRED`. Explicit caller claims of
`ARCHITECTURE_GREEN` or `RUNTIME_PROVEN` are invalid Phase 1 inputs.

Protected classification is deterministic. Any declared path matching the
canonical protected surface, explicit protected intent, private/composite privacy
boundary, or `HIGH`, `CRITICAL`, or `PROTECTED` risk yields
`PROTECTED_REVIEW_REQUIRED`. Ordinary public-safe, low-risk declared scope may
remain public-review-allowed.

The public mapping is policy metadata only. It omits raw payload values,
`touched_files`, `ObservedDiffImpact`, and private runtime values.
