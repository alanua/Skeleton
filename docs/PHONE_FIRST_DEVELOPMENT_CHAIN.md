# Phone-First Development Chain

Stage 0 defines how Skeleton work moves when Oleksii is mostly on a phone. The chain must let tools and workers prepare, execute, test, and summarize work while Oleksii only approves or rejects clear gates.

## Source Of Truth

GitHub Issues remain the task queue. A task starts from an issue with enough scope, allowed files, risk notes, and validation commands for a worker to execute without desktop supervision.

GitHub PRs remain the review and merge surface. Worker output must land as a PR or a clearly linked patch result. Review comments, requested changes, final approval, exact head SHA, and merge history belong in GitHub.

Telegram is only the phone approval console and notification channel. It may summarize status and present approval choices, but it must link back to the GitHub issue or PR for canonical detail.

## Roles

Oleksii is Product Owner, Operator, and approval gate. Oleksii approves or rejects merge, deploy, secrets access, runtime access, production database access, roadmap changes, and any escalation outside the approved issue scope.

ChatGPT plans and reviews. It turns operator intent into issue-ready task shape, checks whether the scope is bounded, reviews results, asks for missing gates, and proposes the next safe action. It does not merge, deploy, handle secrets, or silently change the roadmap.

Runner executes the queue. It picks approved GitHub Issues, prepares controlled task execution, runs the assigned worker under the task contract, captures status, creates or updates the PR surface, and reports back to GitHub and Telegram. Runner does not invent scope, merge without approval, deploy, access secrets, or edit main directly.

Codex is selected for bounded code tasks with allowed files, clear acceptance criteria, and local validation commands. Codex should make the patch, run relevant tests, and return changed files plus verification.

OpenHands is selected for debugging, investigation, and multi-file analysis where the main need is to understand failure shape or coordinate broader code context before a bounded patch is prepared.

Antigravity is a controlled worker candidate for dev cockpit use, repo navigation, patch preparation, and local test support. In stage 0 it is only documented as a possible worker behind a controlled task contract. No Antigravity automation is implemented here.

Gemini is useful for high-risk, security, privacy, large-context, or adversarial review. Its output is advisory until Oleksii accepts it through the GitHub review surface.

Claude is useful for architectural critique, external reasoning, design drift checks, and alternative proposals. Its output is advisory and must be converted into explicit GitHub tasks before execution.

## Safe Gates

- No merge without explicit Oleksii approval.
- No deploy without explicit Oleksii approval.
- No secrets, runtime access, or production database access without explicit Oleksii approval.
- No autonomous roadmap changes.
- No direct edits to `main`; all code changes use a branch and PR.
- Manual PR fallback must validate the exact PR head SHA before merge.
- Approval is scoped to the named issue, PR, action, and head SHA where relevant.

## Phone-First Reports

Every Telegram card, GitHub status report intended for phone review, or final worker summary must use this order:

1. First paragraph: plain human summary.
2. Result/status.
3. Risk.
4. Exactly one recommended next action.
5. Details link or expandable section for logs.

The summary must be short enough to read on a phone without scrolling through logs. It must say what changed or what is blocked before hashes, labels, stack traces, or command output. The recommended next action must be singular: approve, reject, request changes, rerun a named check, or open the linked PR or issue.

## Minimum Automation Contract

Automation may prepare work, run workers, test, create draft PRs, update issue status, and send phone-sized summaries. Automation must stop at approval gates and must keep GitHub as canon for task state and review state.

Worker tasks must state the issue, repository, base branch, allowed files, forbidden actions, acceptance criteria, validation commands, and expected output. A worker that needs broader scope must stop and report `BLOCKED` with one recommended next action.

This stage is documentation and registry only. It adds no runtime enforcement, no Telegram callback changes, no merge automation, no deploy path, no secrets handling, and no Antigravity runner.
