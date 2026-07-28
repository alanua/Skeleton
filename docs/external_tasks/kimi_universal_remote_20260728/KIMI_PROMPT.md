# COPY-PASTE PROMPT FOR KIMI

You are investigating an unresolved universal phone remote problem in the public repository `alanua/Skeleton`.

Read these files in order:

1. https://github.com/alanua/Skeleton/blob/external/kimi-universal-remote-diagnosis-20260728/docs/external_tasks/kimi_universal_remote_20260728/README.md
2. https://github.com/alanua/Skeleton/blob/external/kimi-universal-remote-diagnosis-20260728/docs/external_tasks/kimi_universal_remote_20260728/CURRENT_STATE.md
3. https://github.com/alanua/Skeleton/blob/external/kimi-universal-remote-diagnosis-20260728/docs/external_tasks/kimi_universal_remote_20260728/TASK.md
4. https://github.com/alanua/Skeleton/blob/external/kimi-universal-remote-diagnosis-20260728/docs/external_tasks/kimi_universal_remote_20260728/ACCEPTANCE.md

Then inspect the linked issues, PR, implementation, documentation and tests.

The essential symptom is: the user still sees the old/clumsy remote or an incomplete remote/gamepad experience even though PR #1960 is merged and issue #1966 is closed. Do not assume that closed means fixed. Trace the actual repository-to-runtime serving chain for `GET /remote`, compare the UI payloads to the bounded backend APIs, identify route/runtime/cache drift, and prepare the minimal repository patch plus a bounded live-probe contract.

Do not request private credentials or direct Home Edge access. Do not propose direct SSH or a second executor. Any future live action must be executable by Skeleton through `Skeleton_Home_Edge.home_edge_exec` on `home-edge-01`, with bounded arguments, approval, audit, independent verification and rollback.

Deliver either:

- a GitHub PR against `alanua/Skeleton:main`, linked to the task issue; or
- a unified diff and complete contents of every new file.

Your final report must begin with exactly one of:

`RESULT: DONE`

or

`RESULT: BLOCKED`

and must include root cause, route graph, changed files, tests, bounded probe output schema, deployment preconditions, rollback, and remaining unknowns.
