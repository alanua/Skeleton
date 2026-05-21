# Runner Issue Lifecycle

This document defines the stage 1 issue lifecycle for bounded Runner work in
Skeleton. GitHub issue labels expose queue state; they do not replace operator
approval, review, or explicit gate decisions.

## Labels

- `runner:ready`: The issue is open, bounded, and ready for Runner pickup.
- `runner:running`: Runner has claimed the issue and execution is in progress.
- `runner:review`: Implementation output is waiting for review of the issue,
  pull request, report, validation, and task boundaries.
- `runner:done`: Runner work has completed and is no longer active queue work.
- `runner:blocked`: Runner cannot complete the issue safely or within the task
  boundary.
- `runner:archived`: The issue has been retained as history and removed from
  active work routing.

## Expected Issue States

`runner:ready` is an open issue state for executable queue work. The issue must
state the bounded task, expected output, allowed files, forbidden behavior, and
validation requirements before Runner pickup.

`runner:running` is an open issue state for the single task Runner is actively
executing. Operator actions must not treat it as reviewable output until Runner
posts the report and changes the state.

`runner:review` is an open issue state after implementation output exists and
before an operator decision. Review checks the linked pull request or report,
validation evidence, allowed-file scope, forbidden behavior, and explicit gates.

`runner:done` records completed Runner work. Open issues labeled `runner:done`
are not active work and must not be treated as ready or running queue items.

`runner:blocked` records work that could not complete under the current task
boundary. A blocked issue requires an explicit operator decision to unblock it,
archive it, or supersede it with a new bounded issue.

`runner:archived` records an issue that is no longer an active execution or
review item. Archive history may inform future tasks, but it is not queue input.

## Operator Issue Number Review

When the operator writes an issue number, ChatGPT should:

1. Read the issue.
2. Read the linked pull request and report.
3. Check the allowed files.
4. Check the forbidden behavior.
5. Approve or reject the result against the task boundary and explicit gates.
6. Provide the next issue task.

Approval at this review step does not silently approve merge, deploy, secrets,
runtime or server changes, canon or instruction changes, or private data
exposure.
