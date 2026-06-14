# Hermes Worker v0 Contract

This document defines the public-safe Hermes Worker v0 contract. Version 0 is a
static documentation and schema contract only. It does not add a runtime service,
queue consumer, host process, GitHub workflow, server install, executor daemon,
deployment path, merge path, or canon promotion path.

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

These artifacts are review inputs only. They do not create an active worker.

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

Hermes Worker v0 is contract and evidence only.

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

## Validation

This contract is validated with:

```sh
python -m pytest tests/test_hermes_worker_contract.py
```

Passing tests confirm only that the static public-safe contract and schemas are
present and internally consistent. They do not validate any runtime worker,
service, queue integration, external system, or deployment behavior.
