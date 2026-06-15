# Hermes Hetzner Readiness Check Planning Packet

Status: planning-only public-safe packet for future read-only Hetzner server
readiness checks before any separate Hermes install.

This packet is a review artifact only. It does not connect to Hetzner, inspect
any server, install Hermes, start Hermes, or approve any future command.

The phone Termux Hermes agent is separate and must not be reused, copied,
migrated, coupled to, or treated as the Hetzner Hermes instance. The future
Hetzner Hermes instance is not installed and is not running.

This packet itself grants no authority to connect to Hetzner, install Hermes,
start services, change firewall rules, create a Telegram bridge, create a Runner
bridge, run background processes, mutate server state, or use secrets.

Actual server commands require separate operator approval before they are run.
Approval must name the exact command category, the exact read-only intent, the
expected public-safe output shape, and the stop conditions.

## Purpose

The purpose of this packet is to define safe planning questions for a future
read-only readiness review. The later review may ask whether a Hetzner server
appears suitable for a separate Hermes instance, but this document does not
authorize checking the server.

The planning questions are limited to public-safe categories:

- operating system family and supported release status;
- Python availability and version family;
- disk capacity and free-space summary;
- RAM and swap capacity summary;
- service manager availability;
- location and retention shape of relevant logs;
- backup existence and restore-readiness summary;
- current process inventory at a bounded summary level;
- listening port summary without exposing addresses;
- file and directory permission model at a bounded summary level;
- rollback needs before any future install or service work.

## Proposed Read-Only Check Questions

Future checks may be proposed only as read-only questions. Each proposed check
must be separately approved before execution.

- OS: what supported operating system family and release stream is present?
- Python: is a compatible Python interpreter already available?
- Disk: is there enough free capacity for a future separate Hermes install plan?
- RAM: is memory capacity sufficient for the future planned runtime envelope?
- Service manager: what service manager is available for later review?
- Logs: where would future Hermes logs be expected to go, and what retention
  policy would be required?
- Backup: is there a current backup and a known restore path before any durable
  change is proposed?
- Processes: are there existing processes that would conflict with a future
  Hermes service?
- Ports: are there existing listening ports that would conflict with a future
  bridge or service proposal?
- Permissions: what permission model would be required for future files,
  services, and logs?
- Rollback: what would need to be restored if a later approved install is
  attempted and then stopped?

## Public-Safe Example Command Categories

The examples below are categories only. They are not runnable commands and must
not be expanded into real commands without separate operator approval.

- OS metadata read: a command category that reports operating system family and
  release stream without host identifiers.
- Python version read: a command category that reports interpreter version only.
- Disk summary read: a command category that reports aggregate capacity and free
  space without private paths.
- Memory summary read: a command category that reports aggregate RAM and swap.
- Service manager read: a command category that reports service manager type and
  status capability without service changes.
- Log policy read: a command category that reports whether relevant log storage
  exists without printing raw logs.
- Backup status read: a command category that reports backup existence and
  restore-readiness state without backup contents or private storage details.
- Process summary read: a command category that reports bounded process counts
  and conflict categories without full command lines.
- Port summary read: a command category that reports bounded listening-port
  categories without IP addresses, hostnames, or remote peers.
- Permission summary read: a command category that reports permission classes
  without private host paths or account names.
- Rollback inventory read: a command category that lists future rollback needs in
  generic terms without server identifiers.

## Approval Before Any Command

No command may be run against any server unless a later approval record confirms:

- the exact operator approving the command;
- the exact read-only command category approved;
- the reason the command is needed;
- the expected public-safe output shape;
- the redaction rules that must be applied before any output is shared;
- the stop conditions for that command;
- confirmation that the command does not install, write, delete, restart,
  enable, disable, configure, open, close, upload, download, or change state;
- confirmation that the command does not reveal secrets, private data, server
  identifiers, private paths, raw logs, or full process command lines.

Silence, prior approval, repository tests, this packet, readiness notes, phone
Hermes state, or absence of objections is not operator approval.

## Output Redaction Rules

Future check output must be redacted before it is recorded, summarized, pasted,
or attached to a public issue or pull request.

Required redactions:

- replace IP addresses, hostnames, and server labels with generic placeholders;
- remove account names and private group names;
- remove private host paths and private URLs;
- remove secrets, credentials, tokens, keys, cookies, session material, API
  keys, and authentication headers;
- remove customer data, operator private data, mailbox content, transcripts, and
  raw private payloads;
- remove raw logs unless a separate approval allows a bounded sanitized excerpt;
- remove full process command lines and keep only bounded conflict categories;
- remove remote peers, network endpoints, and provider-specific identifiers;
- summarize disk, RAM, process, port, backup, and permission results as bounded
  public-safe aggregates;
- mark any omitted private field as redacted instead of leaving it blank.

If output cannot be redacted into a public-safe summary, the check must stop and
the evidence must say only that public-safe evidence is unavailable.

## Privacy Boundary

This packet and any future evidence must be public-safe.

Allowed future evidence:

- approved read-only command category names;
- sanitized exit status;
- aggregate capacity, version, count, and readiness summaries;
- public repository paths;
- public issue or pull request identifiers;
- bounded notes that omit sensitive details.

Forbidden future evidence:

- real server identifiers;
- IP addresses, hostnames, server labels, account names, private group names, or
  private host paths;
- secrets, credentials, tokens, keys, cookies, API keys, session material, or
  authentication headers;
- private URLs, raw logs, full process command lines, remote peers, or network
  endpoint details;
- customer data, operator private data, mailbox content, transcripts, private
  prompts, or raw private payloads;
- live server output that has not been reviewed and redacted.

Any privacy-boundary failure blocks the check and any follow-on work.

## Stop Conditions

Future readiness work must stop before any command or output handling that would:

- mutate server state;
- install, upgrade, remove, or configure packages;
- start, stop, restart, enable, disable, or inspect services in a way that
  changes state;
- create, edit, delete, move, upload, download, or chmod files;
- change users, groups, permissions, firewall rules, routes, DNS, ports, network
  policy, or provider settings;
- create a Telegram bridge or Runner bridge;
- create a background process, daemon, timer, queue consumer, or workflow;
- use, print, request, or validate secrets;
- reveal private data, raw logs, private paths, account names, hostnames, IP
  addresses, server labels, remote peers, tokens, keys, or API keys;
- identify the server or its operator;
- produce output that cannot be redacted into a public-safe summary;
- exceed the exact read-only category approved by the operator.

Stop means stop. Do not infer approval from context, tests, prior discussions,
phone Hermes behavior, readiness notes, sanitized examples, or absence of
objections.

## Non-Authority Statement

This packet grants no authority to:

- connect to Hetzner;
- run SSH or any remote shell;
- install Hermes;
- install packages;
- start, stop, restart, enable, disable, or create services;
- change firewall or network settings;
- create a Telegram bridge;
- create a Runner bridge;
- create a background process;
- mutate server state;
- use secrets;
- inspect private data;
- collect live server output;
- approve any future separate Hermes install.

The already merged Hetzner approval packet remains the required approval route
before any install, service, network, Telegram bridge, Runner bridge, background
process, server mutation, or secret use.

## Template Result

Result: planning packet only.

No server connection was made. No server command was run. No install work,
package manager work, service work, background process work, server mutation,
network change, firewall change, Telegram bridge implementation, Runner bridge
implementation, workflow change, runtime change, secret use, private data
access, or live server output collection is approved or performed by this
packet.
