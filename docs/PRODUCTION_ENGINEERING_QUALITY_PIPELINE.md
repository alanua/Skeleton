# Production Engineering Quality Pipeline

Phase 1 is a claim-side compatibility gate. It normalizes live Runner task
vocabulary for review, but it does not grant architecture, production-contract,
runtime, observed-diff, reviewer, merge, deployment, or evidence-authenticity
authority.

## Live Vocabulary

- `green` and `low` normalize to benign public review.
- `yellow` and `medium` normalize to review-relevant, but are not protected by
  color alone.
- `red`, `high`, `critical`, and `protected` normalize to protected review.
- Current public-safe boundaries include
  `PUBLIC_SAFE_CODE_AND_SYNTHETIC_TESTS_ONLY` and
  `PUBLIC_SAFE_SOURCE_AND_SYNTHETIC_TESTS_ONLY`.
- Composite boundaries with private, local, privilege, credential, secret,
  runtime, personal, restricted, or protected portions classify as
  private/protected. Public-safe portions are retained separately for public
  reporting.

## Live Task Shape

The Phase 1 parser preserves supported live routing and validation fields:
`project`, `repo`, `base`, `base_sha`, `branch`, `task_kind`, `payload`,
`requested_capabilities`, `allowed_files`, `forbidden_actions`, `validation`,
`required_tests`, `expected_output`, `privacy` or `privacy_boundary`,
`idempotency` or `idempotency_key`, and `risk` or `risk_level`.

Unknown unsupported semantic fields fail closed. Alias disagreement fails closed.

## Proof Boundary

Phase 1 always reports architecture as `ARCHITECTURE_REVIEW_REQUIRED`,
production contract as `PRODUCTION_CONTRACT_REVIEW_REQUIRED`, runtime as
`RUNTIME_REVIEW_UNREACHED`, and observed diff as `OBSERVED_DIFF_UNREACHED`.
Caller-shaped `ARCHITECTURE_GREEN`, `RUNTIME_PROVEN`, production green receipts,
observed-diff impact, touched files, and runtime proof are rejected as proof.

## Publication Boundary

This phase produces review metadata only. It does not merge, deploy, mutate
runtime, inspect live filesystem state as proof, or integrate with Runner
publication paths.
