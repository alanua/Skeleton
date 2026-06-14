# Hermes Worker v0 Contract

This document defines the public-safe Hermes Worker v0 contract. Version 0 keeps
the static documentation and schema contract only as its authority boundary, with
a local dry-run executor for advisory validation. It does not add a runtime service.

It does not add a queue consumer, host process, GitHub workflow, server install,
executor daemon, deployment path, merge path, or canon promotion path.

## Purpose

Hermes Worker v0 describes how a controlled worker packet may be shaped for
future review. It separates public-safe task context, proposed skill metadata,
validation evidence, and authority boundaries before any runtime implementation
exists.

The worker contract is model-neutral. It must be safe to publish in the
repository and must not depend on private prompts, private URLs, credentials,
secrets, raw private content, hidden transcripts, or host-specific state.

## Contract Artifacts

- `schemas/hermes_task_packet.schema.json` defines the public-safe task packet.
- `schemas/hermes_skill_manifest.schema.json` defines the public-safe skill
  manifest.
- `tests/test_hermes_worker_contract.py` verifies the static contract and
  representative examples.
- `core/hermes_worker.py` exposes a public-safe dry-run executor that reads a
  provided task packet and optional skill manifest, validates contract shape, and
  returns a structured result without performing external actions.
- `tests/test_hermes_worker.py` verifies the dry-run executor.

These artifacts are review inputs only. The dry-run executor is local and
advisory; it does not create an active worker.

## Worker Role

Hermes Worker v0 may be described as a controlled executor candidate with a
skill registry, but this contract grants no execution authority.

A future authorized implementation may use a task packet to understand:

- the public-safe task goal;
- the allowed repository paths;
- the explicitly forbidden actions;
- the requested validation commands;
- the proposed skill manifest;
- the evidence expected from a dry run or review.

## Public-Safe Requirements

Every task packet and skill manifest must remain public-safe.

They must not include:

- secrets, credentials, tokens, keys, or private raw data;
- private data that cannot be safely summarized for public review;
- private URLs, private file paths, or host-specific paths;
- hidden system prompts or model-specific private instructions;
- unbounded transcripts or mailbox exports;
- commands that install servers, start daemons, deploy, merge, push, publish,
  mutate queues, or perform host maintenance.

Public-safe summaries may describe private context only when the sensitive
details are omitted and the remaining claim is still reviewable.

## Authority Boundary

Hermes Worker v0 is contract, dry-run validation, and evidence only.

Hermes Worker v0 must not:

- execute tasks;
- patch files;
- install servers or services;
- start, stop, or manage runtime processes;
- mutate queue, issue, repository, host, deployment, or canon state;
- access or expose secrets;
- approve or activate skills;
- merge, publish, deploy, release, push, or open production routes;
- override operator instructions or reviewed repository canon.

Any action described in a packet is advisory until an authorized operator or
reviewed process accepts it through an existing authority path.

## Task Packet

A Hermes task packet is a bounded public-safe request. It records the task,
scope, allowed files, forbidden actions, validation commands, and output
expectations.

The packet must name the expected worker mode. Version 0 allows only
`review_only`, `dry_run`, or `contract_test`. None of those modes grants live
mutation authority.

The packet must include explicit booleans confirming that it is public-safe,
uses no secrets, performs no runtime mutation, and requires approval before any
durable action.

## Skill Manifest

A Hermes skill manifest is a public-safe description of a proposed skill. It
records what the skill is for, what inputs it expects, what outputs it may
produce, and which boundaries it must respect.

Version 0 skill manifests are not active registrations. A manifest may propose a
skill, but it cannot approve, activate, install, or run that skill.

The manifest must include explicit activation controls:

- `activation_state` must be `proposed`, `review_only`, or `disabled`;
- `approval_required` must be `true`;
- `runtime_install_allowed` must be `false`;
- `network_required` must be `false`.

## Dry-Run Executor

`run_hermes_worker_dry_run(task_packet, skill_manifest=None)` is the only
Hermes Worker v0 executor entry point. It accepts a plain mapping or object and
reads fields directly from that packet. It does not load files, call subprocesses,
read environment variables, access a network, call a model or API, mutate
repositories, mutate queues, or invoke GitHub operations.

The dry-run executor returns only a public-safe structured result with:

- `status`;
- `task_id`;
- `skill_id`;
- `mode`;
- `decision`;
- `warnings`;
- `diagnostics`.

Allowed statuses are:

- `DRY_RUN_OK`;
- `REVIEW_REQUIRED`;
- `OPERATOR_APPROVAL_REQUIRED`;
- `BLOCKED`.

The result intentionally does not echo task goals, titles, validation commands,
source context, private fields, unexpected payload values, or skill body content.
If private-looking field names are present, the executor records only redacted
field names in diagnostics and emits a redaction warning.

## Validation

This contract is validated with:

```sh
python -m pytest tests/test_hermes_worker_contract.py
python -m pytest tests/test_hermes_worker.py
```

Passing tests confirm only that the static public-safe contract and schemas are
present and internally consistent. They do not validate any runtime worker,
service, queue integration, external system, or deployment behavior.
