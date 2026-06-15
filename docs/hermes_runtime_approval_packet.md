# Hermes Runtime Approval Packet Template

Status: planning-only public-safe template for any future Hermes runtime,
install, server, network, or Runner bridge proposal.

This packet template is a review artifact only.

The packet itself grants no execution authority.

It also grants no install authority, runtime authority, service authority,
network authority, server authority, Runner bridge authority, workflow authority,
queue authority, issue authority, merge authority, deployment authority, publish
authority, or canon promotion authority.

Completing this packet does not approve work.

Passing validation does not approve work.

Silence, prior approval, a Hermes recommendation, a dry-run result, or an
existing readiness audit is not operator approval.

Hermes is still not a runtime service.

## Exact Scope

The proposal must define the exact future work being requested before any
implementation begins.

Required operator approval fields:

- Proposal title:
- Public issue or pull request reference:
- Requested future phase:
- Exact task goal:
- Exact systems affected:
- Exact repository paths affected:
- Exact commands proposed:
- Exact durable state changes proposed:
- Exact expected outputs:
- Operator approving this exact scope:
- Approval timestamp:
- Approval channel or record:

Approval is valid only for the exact scope recorded in this section. Any missing,
expanded, ambiguous, private, or host-specific scope blocks the work.

## Allowed Files

The proposal must list every file that may be changed. Files not listed here are
out of scope.

Required operator approval fields:

- Allowed documentation files:
- Allowed schema files:
- Allowed fixture files:
- Allowed test files:
- Allowed script files:
- Allowed workflow files:
- Allowed service files:
- Allowed runtime files:
- Allowed configuration files:
- Operator approving the allowed file list:

If the future proposal includes workflow, service, runtime, server, network, or
Runner bridge files, those paths must be named explicitly and approved before any
edit. A broad directory, wildcard, generated path, hidden path, or private path
is not sufficient approval.

## Forbidden Actions

Unless a later reviewed approval packet explicitly allows a specific action in
the exact scope and allowed files above, the proposal must stop before:

- install work;
- service work;
- network work;
- workflow changes;
- Runner loop changes;
- runtime changes;
- server changes;
- package manager changes;
- daemon, timer, queue consumer, or background process changes;
- queue mutation;
- issue mutation;
- branch publishing, pushing, or pull request creation;
- merge, deploy, release, or canon promotion;
- host maintenance;
- private data access;
- secret access;
- credential, token, key, cookie, or session material access;
- real operator, customer, mailbox, transcript, or production data use.

Required operator approval fields:

- Forbidden-action checklist reviewed by:
- Exceptions requested:
- Exceptions approved:
- Explicit statement that no unapproved forbidden action may occur:

If an action is not explicitly approved, it is forbidden.

## Validation

The proposal must define validation before any implementation begins. Validation
must be public-safe and must not require secrets, private data, private hosts, or
external mutable systems.

Required operator approval fields:

- Pre-change validation commands:
- Implementation validation commands:
- Post-change validation commands:
- Expected passing results:
- Expected failing or blocked results:
- Commands that are confirmed non-mutating:
- Commands that are mutating and require separate approval:
- Operator approving validation:

Validation proves only the stated checks.

Validation never grants execution authority.

Validation does not expand the approved scope.

## Rollback

The proposal must define rollback before any durable action is approved.

Required operator approval fields:

- Exact files or state to restore:
- Exact rollback commands:
- Exact rollback validation commands:
- Evidence to retain after rollback:
- Operator who may authorize rollback:
- Rollback stop conditions:
- Operator approving rollback:

If rollback cannot be expressed in public-safe terms before implementation, the
proposal is not ready.

## Evidence

The proposal must define the public-safe evidence that reviewers and operators
will receive.

Required operator approval fields:

- Evidence packet owner:
- Public repository paths included:
- Public issue or pull request identifiers included:
- Validation commands and results included:
- Scope boundary summary included:
- Forbidden-action summary included:
- Rollback summary included:
- Privacy-boundary summary included:
- Statement that no secrets or private data were used:
- Statement that no unapproved install, service, workflow, Runner loop, runtime,
  network, server, queue, issue, publish, merge, deploy, or canon mutation was
  performed:
- Operator approving evidence capture:

Evidence must be sufficient for a reviewer to reproduce the claim from public
repository state. If evidence cannot be made public-safe, the proposal must stop.

## Privacy Boundary

The packet and all evidence must be public-safe.

Allowed evidence:

- public repository paths;
- public issue or pull request identifiers;
- sanitized command names and exit status;
- aggregate test results;
- synthetic fixtures;
- bounded summaries that omit sensitive details.

Forbidden evidence:

- secrets, credentials, tokens, keys, cookies, or session material;
- raw private data;
- private URLs or private file paths;
- mailbox exports, transcripts, customer content, or real operator private data;
- hidden prompts or model-specific private instructions;
- host-specific diagnostics that cannot be safely published;
- unbounded logs or command output.

Required operator approval fields:

- Privacy reviewer:
- Public-safe summary approved:
- Private data excluded:
- Secrets excluded:
- Host-specific state excluded:
- Operator approving the privacy boundary:

Any privacy-boundary failure blocks the work.

## Stop Conditions

The proposal must stop immediately before any implementation, command, edit,
install, service change, runtime change, network action, server change, workflow
change, Runner bridge change, queue mutation, issue mutation, publish, merge,
deploy, release, or canon promotion when:

- exact operator approval is missing;
- any required operator approval field is blank;
- scope is ambiguous, expanded, or conflicts with a higher-authority source;
- allowed files are missing, broad, private, generated, or incomplete;
- a requested action appears in forbidden actions without explicit approval;
- validation is missing, private, non-reproducible, or mutating without separate
  approval;
- rollback is missing or cannot be made public-safe;
- evidence is missing, private, unbounded, or unreproducible;
- secrets, credentials, tokens, keys, cookies, session material, raw private
  data, private URLs, private paths, transcripts, mailbox exports, or customer
  content are required;
- the work would create or activate a Hermes runtime service without a separate
  reviewed approval packet;
- the work would change Runner behavior without a separate reviewed approval
  packet;
- the operator asks to pause, stop, or re-scope the proposal.

Stop means stop. Do not infer approval from context, tests, prior discussions,
Hermes output, dry-run output, or absence of objections.

## Approval Record

Every future approval packet must include an explicit approval record before
implementation begins.

Required operator approval fields:

- Operator name or approved public-safe identifier:
- Exact approval statement:
- Exact scope approved:
- Exact files approved:
- Exact forbidden-action exceptions approved:
- Exact validation approved:
- Exact rollback approved:
- Exact evidence approved:
- Exact privacy boundary approved:
- Exact stop conditions accepted:
- Date and time:
- Public-safe record location:

The approval record must state: "I approve only the exact scope, allowed files,
validation, rollback, evidence, privacy boundary, and stop conditions recorded in
this packet. This approval does not grant authority for any unlisted install,
service, workflow, Runner loop, runtime, network, server, queue, issue, publish,
merge, deploy, or canon action."

## Template Result

This template is complete only when every required operator approval field above
is filled with public-safe information and reviewed through an existing
authorized process.

Until then, the only approved result is planning review.

No execution authority is granted by this packet template.
