# Runner Retry Policy

Runner retry handling is fail-closed for repeated blockers. Before an issue is
claimed as `runner:running`, the poller classifies the task route and inspects
prior Runner blocked comments for retry metadata.

Routes:

- `code_generation`: fenced Codex worktree tasks.
- `publish_only`: issue-worktree publish and inspect/publish handlers,
  including `publish_existing_issue_worktree`.
- `runtime_only`: runtime maintenance and Telegram merge tasks.

`runtime_only` and `publish_only` tasks are dispatched through maintenance
handlers only. Relabelling one of those issues must not invoke Codex.

Code-generation tasks must include a non-empty, non-placeholder
`expected_output` before Codex is invoked. The poller accepts `Expected Output:`
metadata, multiline `expected_output:` metadata, and fenced YAML metadata before
the `task` fence.

Blocked reports include:

- `route=<route>`
- `retry_decision=<decision>`
- `retry_attempt=<n>`
- `blocker_signature=<sha256-prefix>`
- `changed_condition=<true|false>`
- `override_used=<true|false>`
- `next_required_action=<DIAGNOSE|PUBLISH_ONLY|RUNTIME_ONLY>` when operator
  action is needed

The blocker signature is derived from stable, public-safe condition fields:
route, maintenance task id, allowed-file set, expected output, explicit
dependency state, bounded status fields, and a normalized blocker reason.
Timestamps, raw command output, local paths, environment values, secrets,
tokens, customer/project-private data, quantities, and volatile text are
redacted or ignored before hashing.

If the latest two relevant blocked reports have the same signature, Runner
posts `NEEDS_OPERATOR` with `reason=repeated_blocker` and does not invoke Codex,
a provider, or the same maintenance handler again.

If prior Runner history cannot be verified, Runner posts `NEEDS_OPERATOR` with
`reason=prior_runner_history_unverifiable` rather than assuming this is a first
attempt.

Operators may allow one more attempt by adding both fields to the issue body:

```text
Retry Override: <opaque-token>
Retry Reason: <bounded-public-safe-reason>
```

Each override token is hashed in the public result after use. Reusing the same
token is rejected and returns `NEEDS_OPERATOR`.

Material condition changes reset the guard by producing a different signature:
route, maintenance task id, allowed-file set, expected output, explicit
dependency state, or blocker reason. Cosmetic issue-body edits do not reset it.


## Actual blocker binding

The stable condition signature represents the task route and bounded static
execution scope. The blocker signature is bound when a failure report is
created, using the actual bounded failure reason or final blocked marker.

Two consecutive reports with the same condition signature and blocker signature
stop the next execution before Codex or maintenance dispatch. A different actual
blocker reason is treated as a changed condition.


## Trusted retry-history authors

GitHub comment objects contribute retry history only when their author is the
repository owner, the explicitly configured Runner actor, or an allowlisted bot
identity. Missing and untrusted authors are rejected fail-closed. Raw string
reports remain accepted only for internal unit-test fixtures.
