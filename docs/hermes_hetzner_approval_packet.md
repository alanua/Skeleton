# Hermes Hetzner Approval Packet Template

Status: planning-only public-safe template for a future separate Hermes
instance on a Hetzner server.

This document is a review artifact only.

The document itself grants no authority to install or run Hermes.

Do not include server IPs, hostnames, user names, secrets, private paths,
tokens, API keys, or private runtime data.

Completing this packet does not approve work.

Passing validation does not approve work.

No server install, service setup, network access, Telegram bridge, Runner
bridge, background process, or secret use is approved by this template.

The phone Termux Hermes agent and the future Hetzner Hermes instance are
separate systems. The phone Hermes agent must not be reused, copied, migrated,
or treated as approval for the Hetzner Hermes instance. The Hetzner Hermes
instance must be planned as its own controlled server-side component.

## Scope

The proposal must describe the exact future Hetzner Hermes work requested before
any implementation begins.

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
- Confirmation that phone Hermes is out of scope:
- Confirmation that Hetzner Hermes is a separate controlled component:
- Operator approving this exact scope:
- Approval timestamp:
- Approval channel or record:

Approval is valid only for the exact scope recorded here. Missing, ambiguous,
expanded, private, host-specific, or runtime-specific scope blocks the work.

## Allowed Files

The proposal must list every public repository file that may be changed. Files
not listed here are out of scope.

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

A broad directory, wildcard, generated path, private path, server path, or hidden
path is not sufficient approval. Any future workflow, service, runtime, server,
network, Telegram bridge, Runner bridge, background process, or secret-related
file must be named explicitly and approved before any edit.

## Forbidden Actions

Unless a later reviewed approval packet explicitly allows a specific action in
the exact scope and allowed files above, the proposal must stop before:

- install work;
- package manager work;
- service work;
- systemd, timer, daemon, queue consumer, or background process work;
- server mutation;
- network access;
- network change;
- firewall change;
- SSH setup or remote shell use;
- Telegram bridge implementation;
- Runner bridge implementation;
- workflow changes;
- runtime changes;
- phone Hermes reuse, migration, or coupling;
- queue mutation;
- issue mutation;
- branch publishing, pushing, or pull request creation;
- merge, deploy, release, or canon promotion;
- private data access;
- secret access;
- credential, token, key, cookie, or session material access;
- real server identifiers, hostnames, IP addresses, user names, private paths,
  tokens, API keys, or private runtime data.

Required operator approval fields:

- Forbidden-action checklist reviewed by:
- Exceptions requested:
- Exceptions approved:
- Explicit statement that no unapproved forbidden action may occur:

If an action is not explicitly approved, it is forbidden.

## Validation

The proposal must define validation before any implementation begins. Validation
must be public-safe and must not require secrets, private data, private hosts,
live services, external mutable systems, or network mutation.

Required operator approval fields:

- Pre-change validation commands:
- Implementation validation commands:
- Post-change validation commands:
- Expected passing results:
- Expected failing or blocked results:
- Commands confirmed non-mutating:
- Commands that are mutating and require separate approval:
- Operator approving validation:

Validation proves only the stated checks.

Validation never grants install authority, runtime authority, service authority,
server authority, network authority, Telegram bridge authority, Runner bridge
authority, background process authority, or secret-use authority.

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
- Phone Hermes separation summary included:
- Forbidden-action summary included:
- Rollback summary included:
- Privacy-boundary summary included:
- Statement that no secrets or private data were used:
- Statement that no unapproved install, service setup, network access, network
  change, firewall change, Telegram bridge, Runner bridge, background process,
  server mutation, workflow change, queue mutation, issue mutation, publish,
  merge, deploy, or canon mutation was performed:
- Operator approving evidence capture:

Evidence must be sufficient for a reviewer to reproduce the claim from public
repository state. If evidence cannot be made public-safe, the proposal must
stop.

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

- server IPs, hostnames, user names, or private host paths;
- secrets, credentials, tokens, keys, cookies, API keys, or session material;
- raw private data;
- private URLs or private file paths;
- mailbox exports, transcripts, customer content, or real operator private data;
- hidden prompts or model-specific private instructions;
- host-specific diagnostics that cannot be safely published;
- unbounded logs or command output;
- private runtime data from phone Hermes or any future Hetzner Hermes process.

Required operator approval fields:

- Privacy reviewer:
- Public-safe summary approved:
- Private data excluded:
- Secrets excluded:
- Real server identifiers excluded:
- Host-specific state excluded:
- Operator approving the privacy boundary:

Any privacy-boundary failure blocks the work.

## Operator Approval

Every future approval packet must include an explicit operator approval record
before implementation begins.

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

Silence, prior approval, phone Hermes state, a Hermes recommendation, a dry-run
result, readiness notes, validation output, or absence of objections is not
operator approval.

## Stop Conditions

The proposal must stop immediately before any implementation, command, edit,
install, service setup, network access, network change, firewall change,
Telegram bridge work, Runner bridge work, background process work, server
mutation, workflow change, runtime change, queue mutation, issue mutation,
publish, merge, deploy, release, or canon promotion when:

- exact operator approval is missing;
- any required operator approval field is blank;
- scope is ambiguous, expanded, private, or conflicts with a higher-authority
  source;
- phone Hermes would be reused, copied, migrated, coupled, or treated as the
  Hetzner Hermes instance;
- allowed files are missing, broad, private, generated, server-specific, or
  incomplete;
- a requested action appears in forbidden actions without explicit approval;
- validation is missing, private, non-reproducible, mutating, or requires live
  service access without separate approval;
- rollback is missing or cannot be made public-safe;
- evidence is missing, private, unbounded, or unreproducible;
- secrets, credentials, tokens, keys, cookies, API keys, session material, raw
  private data, private URLs, private paths, transcripts, mailbox exports,
  customer content, real server identifiers, or private runtime data are
  required;
- the work would create, install, start, stop, restart, enable, disable, or
  connect a Hermes runtime service without a separate reviewed approval packet;
- the work would change Runner behavior without a separate reviewed approval
  packet;
- the operator asks to pause, stop, or re-scope the proposal.

Stop means stop. Do not infer approval from context, tests, prior discussions,
phone Hermes behavior, Hetzner readiness notes, dry-run output, or absence of
objections.

## Safe First Server-Readiness Checks

These checks are planning gates only. They do not authorize connecting to,
installing on, mutating, or running anything on a server.

Before any live server check is proposed, a separate reviewed approval packet
must define:

- the exact non-mutating command or observation requested;
- why the check is needed before implementation;
- who may run the check;
- what public-safe output may be retained;
- what private output must be excluded;
- what result allows planning to continue;
- what result requires stopping;
- confirmation that no install, service setup, network change, firewall change,
  Telegram bridge, Runner bridge, background process, or secret use will occur.

Safe first readiness planning may include public-safe questions such as whether
a future server-side component needs disk, memory, operating system, service
manager, logging, monitoring, backup, access-control, and rollback requirements.
It must not include real server identifiers, private host paths, secrets, tokens,
API keys, live credentials, private runtime data, or instructions to connect to a
server.

## Template Result

No execution authority is granted by this packet template.

No install authority is granted by this packet template.

No service authority is granted by this packet template.

No network authority is granted by this packet template.

No Telegram bridge authority is granted by this packet template.

No Runner bridge authority is granted by this packet template.

No background process authority is granted by this packet template.

No secret-use authority is granted by this packet template.

No phone Hermes reuse is granted by this packet template.

The only valid result of completing this template is a public-safe planning
artifact ready for separate operator review.
