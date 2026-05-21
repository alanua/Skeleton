# Skeleton Development Department

Stage 1 defines the Skeleton Development Department as a human-controlled
code-production workflow built on bounded GitHub issue tasks, Runner, Codex,
pull requests, validation, reports, and operator decisions.

The Development Department organizes practical code production. It does not
grant Skeleton authority to expand its own scope, change its own controls, or
promote work into live behavior without the required human gates.

## Roles

### Intake

Intake turns an operator request into an explicit candidate task. Intake keeps
the request bounded, identifies missing context, and separates work that belongs
in Skeleton from work that belongs in another project or product.

### Planner

Planner defines the task boundary, expected outputs, allowed files, forbidden
behavior, validation commands, and review points before implementation starts.

### Gatekeeper

Gatekeeper checks the task against Skeleton controls before execution and again
before any sensitive transition. Gatekeeper keeps approval authority with the
operator where explicit gates apply.

### Runner/Codex Implementer

Runner/Codex Implementer executes the approved issue task inside the controlled
Runner/Codex route, produces the bounded change, and reports what happened.

### Validator

Validator checks requested validation, scope limits, and generated evidence. A
validation result is evidence for review, not permission to merge, deploy, or
cross a sensitive gate.

### Reviewer

Reviewer reads the issue, change, pull request, report, validation evidence,
allowed-file boundary, and forbidden-behavior boundary before recommending
approval or rejection.

### Reporter

Reporter records implementation outcome, validation result, blocks, residual
risk, and the next bounded task that can enter the queue.

### Operator

Operator owns requests, approval, rejection, unblock decisions, gate decisions,
and final direction. Skeleton does not replace the operator.

## Normal Flow

1. The operator submits a bounded request.
2. The request becomes an issue task with scope, allowed files, forbidden
   behavior, and validation requirements.
3. Runner/Codex executes the issue task through the GitHub issue queue.
4. Implementation produces a pull request and report, or a blocked report when
   the task cannot complete safely.
5. Validator checks the requested validation and scope evidence.
6. Reviewer checks the issue, pull request, report, validation, gates, and
   boundary rules.
7. The operator approves or rejects the result and decides the next bounded
   issue task.

## Explicit Gates

These transitions require explicit operator control and must not be inferred
from a successful implementation or validation report:

- Merge.
- Deploy.
- Secrets access, creation, movement, or exposure.
- Runtime or server changes.
- Canon or instruction changes.
- Private data exposure.

## Operating Rules

- Skeleton builds through bounded tasks, not autonomous self-expansion.
- Skeleton is not Jeeves.
- Jeeves autonomy belongs to future product work, not current Skeleton
  behavior.
- Normal execution goes through the GitHub issue queue, not manual Hetzner
  command suggestions.
- Runner/Codex execution may prepare code, documentation, pull requests,
  validation evidence, and reports only within the task boundary.
- Sensitive gates stay human-controlled even when an implementation task
  completes successfully.
