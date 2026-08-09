# Autonomous Review Gate

Skeleton uses one internal review gate for ordinary Runner PR and task outcomes.
The gate replaces operator relay prompts such as "awaiting approval" or
"check in ChatGPT" for routine Runner-generated PRs.

The gate reads current PR metadata, head SHA, changed files, compare state,
validation status, delegated merge policy, scope and privacy flags, and review
findings before recording a verdict. It does not rely on a stale `runner:done`
label alone.

Verdicts:

- `APPROVE`: all internal checks passed. The existing authorized continuation
  path may proceed, subject to merge, deploy, protected-file, and authority
  policies that already apply.
- `REQUEST_CHANGES`: bounded repair is needed. Skeleton records one idempotent
  repair task keyed by repository, PR, head SHA, reason, and findings, reusing
  the existing PR and branch when possible.
- `DO_NOT_MERGE`: the PR is internally held for repair, supersede, or dependency
  handling. This remains internal and does not notify Telegram.
- `NEEDS_OPERATOR`: a true human authority boundary was reached, such as
  protected files, new product or governance authority, physical action, or a
  security or secret boundary. Only this routine escalates to Jeeves/operator
  notification.

GitHub review API limitations, including inability to self-submit a formal
`APPROVE` or `REQUEST_CHANGES` review, do not block continuation. The typed
internal receipt is durable enough to drive the next policy-authorized step.

Routine review outcomes do not send Telegram notifications. Telegram remains
reserved for true `NEEDS_OPERATOR` events and existing explicit operator-action
flows.
