# Skeleton Governance Contract v1

This contract defines the public-safe governance surface for Skeleton. It is a
static documentation and schema contract only. It does not add runtime
enforcement, deployment behavior, merge behavior, server changes, secrets
handling, or live network routes.

## Scope

The v1 governance contract covers:

- authority ladder for conflicting sources;
- memory boundary between weak cache, handoff state, and canon;
- role manifest for operator, planner, executor, runner, and auditor roles;
- evidence packet required before durable governance decisions;
- task template for bounded work assignment;
- gate fixtures for expected allow and block outcomes.

## Authority Ladder

When sources conflict, use this order:

1. Latest direct operator instruction for the current task.
2. GitHub `main` and reviewed project files.
3. Project `PROJECT_MANIFEST.yaml`.
4. Project `STATE.yaml` as handoff state.
5. NotebookLM sourcepack as a read-only mirror.
6. Model memory as weak cache.

Lower sources can help find context, but they cannot override a higher source.
If the active worktree, sourcepack, local notes, or memory disagree with GitHub
`main`, treat GitHub `main` as canon after freshness is checked.

## Memory Non-Canon Boundary

Memory is operational state and weak recall unless promoted through an explicit
approved canon route. Memory may record public-safe task lifecycle facts,
sanitized evidence summaries, and opaque private reference stubs. Memory must
not store secrets, `.env` values, raw private documents, Drive or Docs URLs,
private file paths, raw executor transcripts, or customer content.

`STATE.yaml` is handoff state, not canon truth. NotebookLM sourcepacks are
read-only mirrors, not canon. A canon candidate becomes canon only after
operator-approved promotion into reviewed repository files.

## Role Manifest

The role manifest identifies which actor may plan, execute, review, or approve
work. A role may have multiple allowed actions, but authority still follows the
authority ladder.

- `operator`: sets current instructions, approves durable writes, merge, deploy,
  and canon promotion.
- `planner`: reads project context, critiques scope, prepares task templates,
  and does not perform durable writes unless also assigned as executor.
- `executor`: makes bounded file changes in the assigned worktree and reports
  evidence.
- `runner`: owns worktree orchestration, validation, branch and PR mechanics
  when explicitly allowed.
- `auditor`: reviews diffs, evidence, and risks; audit output is advisory unless
  the operator makes it binding.

See `schemas/skeleton_role_manifest.schema.json` for the machine-readable
shape.

## Evidence Packet

Before a governance decision can be treated as reviewable, the packet must
include:

- active project and repository;
- task source and current operator instruction;
- authority sources read;
- requested files to change;
- actual files changed;
- memory boundary assessment;
- risk summary;
- validation performed;
- explicit prohibited areas not touched.

Evidence packets are public-safe summaries. They must not embed secrets, raw
private content, private data locators, or unbounded executor transcripts.

## Task Template

A governance task assignment must include:

- task id, project id, repository, and worktree;
- goal, scope, forbidden areas, and stop condition;
- files or directories allowed to change;
- expected evidence packet path or destination;
- validation expectations;
- approval requirements.

The task template is a planning and handoff artifact only. It does not grant
merge, deploy, publish, or runtime authority by itself.

## Gate Fixtures

Fixtures under `fixtures/governance/` document expected static gate outcomes:

- `gate_allow_public_docs_only.json` is allowed because it changes only docs,
  schemas, and fixtures and declares no runtime side effects.
- `gate_block_runtime_scope.json` is blocked because it requests runtime/server
  changes outside the governance contract scope.
- `evidence_packet_minimal.json` is a minimal public-safe evidence packet.
- `role_manifest_v1.json` is a minimal role manifest for v1.
- `task_template_public_docs_only.json` is a bounded task template example.

These fixtures are examples for future validation. They are not live gates.

## V1 Non-Goals

This contract does not implement runtime enforcement, deploy automation,
repository merge automation, server changes, secret handling, private-data
storage, or publishing. Any live enforcement stage must be proposed in a
separate task with explicit operator approval, implementation tests, and a
small reviewed scope.
