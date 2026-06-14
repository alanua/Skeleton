# Hermes Readiness Audit

Status: public-safe readiness audit for Hermes Worker v0.

This audit summarizes only repository-visible Hermes Worker v0 state. It does
not rely on private transcripts, private paths, host state, credentials,
operator-only diagnostics, or unpublished implementation details.

This audit does not authorize install work, service work, workflow changes,
Runner loop changes, runtime changes, network work, issue mutation, queue
mutation, publishing, merging, deployment, or canon promotion.

## Repository-Visible State

Hermes Worker v0 currently exists as public-safe contract, schema, fixture,
dry-run, and test artifacts:

- `docs/hermes_worker_v0.md` defines the public-safe worker contract and states
  that v0 is static contract, dry-run validation, and evidence only.
- `docs/HERMES_AUTHORITY_BOUNDARY.md` grants Hermes review and evidence
  authority only.
- `projects/skeleton/HERMES_WORKER_V0_PLAN.md` says the current phase is public
  contract and schemas only, with future phases requiring separate review and
  approval before implementation.
- `schemas/hermes_task_packet.schema.json` defines public-safe task packets.
- `schemas/hermes_skill_manifest.schema.json` defines public-safe skill
  manifests.
- `core/hermes_worker.py` exposes a local dry-run executor that validates
  provided packets and manifests without external actions.
- `fixtures/hermes_worker/` contains synthetic public-safe worker fixtures.
- `tests/test_hermes_worker_contract.py`, `tests/test_hermes_worker.py`, and
  `tests/test_hermes_worker_fixtures.py` verify the visible contract, dry-run
  executor, and fixtures.

The repository-visible state does not include an active Hermes service, queue
consumer, daemon, workflow, deployment path, live skill registry, runtime
install route, publish route, merge route, or production Runner executor route.

## No-Secrets And No-Private-Data Boundary

All readiness evidence for Hermes Worker v0 must be public-safe before it is
recorded in the repository.

Allowed evidence may include:

- public repository paths;
- public issue or pull request identifiers when already visible;
- aggregate test results;
- sanitized command names and exit status;
- synthetic fixtures;
- bounded summaries that omit sensitive details.

Forbidden evidence includes:

- secrets, credentials, tokens, keys, cookies, or session material;
- raw private data, mailbox exports, transcripts, prompts, or customer content;
- private URLs or private file paths;
- host-specific state that cannot be safely published;
- unbounded logs or command output;
- any material that would expose operator-only diagnostics.

When evidence cannot be made public-safe, the audit result must say that the
evidence is unavailable for public review and stop before any install or runtime
step.

## Readiness Checks Before Future Install Or Runtime Work

These checks must pass before any future Hermes install, service, workflow,
Runner bridge, live skill activation, queue integration, or runtime work is
proposed:

1. Confirm the requested future work is backed by a reviewed public issue or
   pull request that names the exact scope.
2. Confirm the allowed files, forbidden actions, validation commands, and
   expected outputs are public-safe and explicit.
3. Confirm the task packet keeps `public_safe`, `no_secrets`,
   `no_runtime_mutation`, and `approval_required` set to true.
4. Confirm the skill manifest keeps `approval_required` true,
   `runtime_install_allowed` false, and `network_required` false unless a later
   reviewed change explicitly changes the contract.
5. Confirm no command installs packages, starts daemons, changes workflows,
   mutates queues, mutates issues, publishes branches, opens pull requests,
   merges, deploys, or promotes canon.
6. Confirm dry-run output stays advisory and does not echo private payload
   values, hidden prompt text, private paths, or secrets.
7. Confirm rollback instructions and evidence capture are defined before any
   durable action is approved.
8. Confirm an authorized operator has approved the exact next step after
   reviewing the public-safe evidence packet.

Failure of any readiness check blocks the future install or runtime work until a
new reviewed change resolves the gap.

## Operator Approval Gates

Hermes readiness requires explicit operator approval gates. Approval must be
recorded before each higher-risk transition:

- Gate 1: approve public-safe audit evidence only.
- Gate 2: approve any proposed contract or schema change.
- Gate 3: approve any proposed dry-run or fixture expansion.
- Gate 4: approve any proposed skill manifest activation path.
- Gate 5: approve any proposed install, service, workflow, Runner bridge,
  network, queue, issue, publish, merge, deploy, or canon promotion path.

Approval at one gate does not approve the next gate. Silence, passing tests, a
Hermes recommendation, or a dry-run result is not operator approval.

## Rollback Requirements

Any future change beyond public-safe audit documentation must include a rollback
plan before it is approved.

The rollback plan must name:

- the exact files, schemas, fixtures, or commands that would be reverted;
- the state that must be restored;
- the validation commands that prove restoration;
- the operator who may authorize rollback;
- the evidence that must be retained after rollback.

If rollback cannot be expressed without private data or host-specific state, the
work is not ready for public repository implementation.

## Evidence Requirements

Every readiness claim must be supported by public-safe evidence.

Required evidence includes:

- repository paths for every referenced contract, schema, fixture, or test;
- the validation commands used;
- test results or explicit reasons tests were not run;
- a summary of scope boundaries and forbidden actions;
- a statement that no secrets or private data were used;
- a statement that no install, service, workflow, Runner loop, runtime, network,
  queue, issue, publish, merge, deploy, or canon mutation was performed.

Evidence must be sufficient for a reviewer to reproduce the claim from the
repository without access to private systems.

## Audit Result

Hermes Worker v0 is ready only for continued public-safe contract review,
schema review, fixture review, and dry-run evidence review.

Hermes Worker v0 is not ready for install work, service work, workflow changes,
Runner loop changes, runtime changes, network work, live skill activation, queue
integration, issue mutation, publishing, merging, deployment, or canon
promotion without a separate reviewed change and explicit operator approval.
