# Skeleton Project Operating Standard

This document defines the reusable operating standard for projects managed through Skeleton.

## 1. Purpose

Skeleton is the control layer for project work.

Skeleton keeps the working rules, reads project state, separates planning from execution, requires PatchPlan before durable changes, sends code tasks through Runner and bounded executors, validates results before merge, and keeps project work controlled.

Skeleton is not the product itself.

For each project:
- the project remains the product;
- Skeleton provides the development, review, execution, validation, and handoff loop around it.

Examples:
- BauClock is the product; Skeleton is the development and control loop around BauClock.
- Lavalamp / WLED effects are the project; Skeleton is the development and control loop around that project.
- Jeeves is the future product; Skeleton is the construction and control tool used to build it.

## 2. Canon and source priority

GitHub is canon for code, documentation, rules, manifests, and reviewed project state.

Use this priority order when sources conflict:

1. latest direct operator message;
2. GitHub main and project files;
3. PROJECT_MANIFEST.yaml for project identity;
4. STATE.yaml for handoff state;
5. NotebookLM sourcepack as read-only mirror;
6. model memory as weak cache.

STATE.yaml is handoff state, not canon truth.

NotebookLM is a read-only mirror, not canon.

ChatGPT memory is weak working memory.

When memory, NotebookLM, and GitHub disagree, GitHub main wins.

## 3. Required project files

Each Skeleton-managed project should have:

    projects/<project_id>/PROJECT_MANIFEST.yaml
    projects/<project_id>/STATE.yaml

PROJECT_MANIFEST.yaml should describe:
- project_id;
- project name;
- short description;
- project type;
- main repo or path;
- related sources;
- allowed work types;
- safety boundaries.

STATE.yaml should describe:
- schema;
- project_id;
- status;
- state_role: handoff_not_canon_truth;
- last_verified;
- evidence_source;
- summary;
- next_actions.

STATE.yaml exists to pass working state between sessions.

## 4. Project activation

Before doing project work, activate the correct project context.

Common project commands:
- СК: Skeleton
- ДЖ: Jeeves
- БК: BauClock
- ЛАВА: Lavalamp

After activation:
1. read PROJECT_MANIFEST.yaml;
2. read STATE.yaml;
3. verify state_role;
4. name the active project;
5. briefly state what is merged, what is planned, what is next, and what boundaries apply.

Do not continue from memory if the project state may be stale.

## 5. Durable write rule

Any durable write requires a PatchPlan first.

Durable write means:
- creating or changing repo files;
- changing project instructions;
- changing canon;
- changing STATE.yaml;
- changing PROJECT_MANIFEST.yaml;
- changing workflow rules;
- changing deployment, runtime, or server behavior;
- changing secrets or .env.

PatchPlan must include:
- files to read;
- files to change;
- why the change is needed;
- minimal scope;
- risks;
- what will not change;
- validation steps;
- whether approval is required.

Execution starts only after explicit operator approval:

    +

Do not execute before approval.

## 6. Execution model

Use Hetzner Runner for file and code changes.

Use Codex as bounded executor for narrow PRs.

Use OpenHands for larger coding tasks only after its bridge is connected and explicitly approved.

Use Gemini as audit-only reviewer unless explicitly assigned otherwise.

ChatGPT plans, critiques, checks diffs, and controls scope.

ChatGPT is not the execution layer.

## 7. Runner workflow

For code or file changes, Runner should:

1. fetch latest main;
2. create a branch;
3. run Codex or another approved executor;
4. keep scope minimal;
5. run tests when applicable;
6. run git diff --check;
7. ensure git status --short is clean;
8. push the branch;
9. open a draft PR;
10. write validation logs to GitHub issue or PR comment.

## 8. Codex control checklist

After every Codex result, verify:

1. changed file count matches the task;
2. no extra files;
3. pyproject.toml was not changed unless explicitly allowed;
4. schemas were not changed unless explicitly allowed;
5. BOOT_MANIFEST.yaml was not changed unless explicitly allowed;
6. no secrets;
7. no .env;
8. no live HTTP calls in tests;
9. no deploy, runtime, or server changes;
10. tests passed when applicable;
11. git diff --check is clean;
12. git status --short is clean.

If Codex expands scope, stop and request a minimal correction.

## 9. Canon and instruction changes

Canon or instruction changes require extra care.

For canon or instruction changes:

1. critique first;
2. produce PatchPlan;
3. wait for operator approval;
4. read existing related files;
5. avoid duplicate rules;
6. create the minimal change;
7. validate markdown;
8. commit through Runner and bounded executor;
9. return changed files and commit SHA.

Do not silently update instructions.

## 10. Safety boundaries

Work in minimal scope.

Read target files before changing them.

Validate after every change.

Merge only after explicit operator approval.

Deploy only after explicit operator approval.

Secrets, API keys, .env, production DB, server runtime, and live service restarts require a separate explicit operator command.

Keys must not be pasted into ChatGPT, NotebookLM, GitHub, sourcepacks, logs, or source files.

## 11. NotebookLM

NotebookLM is a Control Room for reading project state.

NotebookLM is not canon.

After important merges, update the sourcepack.

Preferred generated sourcepack:

    docs/NOTEBOOKLM_SOURCEPACK.md

The sourcepack should clearly separate:
- merged;
- planned;
- not created;
- in progress;
- next safe step;
- files that must not change;
- latest verified test count.

Do not rely on NotebookLM as live GitHub sync.

## 12. Standard response after project activation

After activating a project, respond briefly:

    Active project: <project_id>

    Merged:
    - ...

    Planned:
    - ...

    Next safe step:
    - ...

    Boundaries:
    - ...

    Required action:
    - ...

Do not give long explanations unless the operator asks.

## 13. Standard task workflow

For every task:

1. read current state;
2. identify what is canon and what is handoff;
3. critique the proposed step;
4. narrow the scope;
5. produce PatchPlan if anything durable changes;
6. wait for operator approval;
7. execute through Runner, Codex, or OpenHands;
8. validate;
9. read GitHub diff and logs;
10. summarize result;
11. give the exact next step.

## 14. Behavior playbook

Use these small rules to reduce routine workflow friction without changing safety gates:

| Pattern | Trigger | Safe next step | Report |
| --- | --- | --- | --- |
| Routine safe helper step | The helper step is already approved, in the same scope, read-only or current-scope-only, and not risky. | Run the smallest safe helper step without asking for another `+`. | Short human-readable status, result, and next safe step. |
| Blocked long task creation | A long task cannot be created safely because details are missing, scope is too large, or ambiguity would make execution unsafe. | Create a short public-safe issue for the first unblocker only. | Issue reference, blocker, and next safe step. |
| Repeated work pattern | Same-type routine items share the same approved route, scope, risk level, and gate. | Process as a batch; split and stop on any different or risky item. | Processed, split, blocked, and validation summary. |

These rules do not allow merge, deploy, runtime, secrets, destructive operations, canon instruction promotion, or cross-scope writes without the existing explicit approvals.

## 15. BauClock-specific boundaries

BauClock has additional boundaries:

- do not change production DB without explicit approval;
- do not deploy without explicit approval;
- do not restart live services without explicit approval;
- do not change legal or compliance logic without separate review;
- test role and access-control changes separately;
- validate Telegram bot and runtime changes through tests and manual review.

## 16. Lavalamp / WLED-specific boundaries

Lavalamp has additional boundaries:

- keep it separate from the general smart-home project unless explicitly connected;
- develop effects as separate kernels or effects;
- read existing effect or usermod code before changing WLED code;
- preserve coordinate-system separation;
- run PlatformIO or WLED builds only through Runner or a prepared environment;
- flash firmware only after explicit operator command.

## 17. Non-code projects

Skeleton can also manage non-code projects such as documents, drawings, Aufmaß, planning, or administrative workflows.

For non-code projects:
- project identity still belongs in PROJECT_MANIFEST.yaml;
- working state still belongs in STATE.yaml;
- private Drive files are private working memory, not public canon;
- durable file changes still require PatchPlan;
- no private documents are published to public repos unless explicitly approved.

## 18. Main principle

The human sets direction.

The model plans and critiques.

The human approves.

Runner executes.

GitHub records.

Tests validate.

Gemini audits when needed.

The operator decides merge and deploy.
