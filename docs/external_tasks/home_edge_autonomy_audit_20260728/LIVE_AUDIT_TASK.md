# LIVE AUDIT TASK — Home Edge full autonomy inventory

## Mode

`RUNTIME_MAINTENANCE_TASK`

## Target

- node: `home-edge-01`
- execution: `Skeleton_Home_Edge.home_edge_exec` only
- initial risk: green, read-only
- operator approval: `EXPLICIT_HOME_EDGE_FULL_AUTONOMY_AUDIT_20260728`

## Objective

Inspect the actual Home Edge node and all registered home-network functions. Determine exactly what is autonomous and healthy, what is manual-only, installed but inactive, configured but unverified, broken, blocked, stale or missing. Do not infer live state from repository or issue closure.

## Mandatory boot/read order

1. read the embodied MemoryGate record;
2. run device registry doctor;
3. list devices;
4. list changes;
5. list operations;
6. inspect relevant devices individually;
7. inspect the canonical state database and registered paths;
8. read prior failure history before any retry proposal;
9. compare live runtime identity/hashes with current approved repository state.

## Phase 1 — read-only node inventory

Collect public-safe aggregates for:

- OS identity, uptime, time synchronization, load, memory, disk and inode capacity;
- failed systemd units, enabled services/timers, restart policies and duplicate workers;
- required user session/DBus persistence;
- Tailscale/control-route reachability without printing private addresses;
- executor health, execution lanes, approvals and recent audit receipts;
- MemoryGate readability and last durable update;
- device registry integrity, backup age and state DB integrity;
- stale/expired state rows, identity-pending devices and unverified paths;
- all operations grouped by risk, last success/failure and verification level;
- queue/outbox depth, oldest age, retries, quarantine and stuck claims;
- runtime releases, deployed hashes, backup/rollback availability and drift from approved main.

## Phase 2 — subsystem audit

For every subsystem, assign one exact state:

- `READY_AUTONOMOUS`
- `READY_MANUAL_TRIGGER`
- `INSTALLED_NOT_ENABLED`
- `ENABLED_NOT_HEALTHY`
- `CONFIGURED_NOT_VERIFIED`
- `BROKEN`
- `BLOCKED_APPROVAL`
- `STALE_UNKNOWN`
- `MISSING`

Audit at least:

### Control plane

- `home_edge_exec` and registered execution lanes;
- gateway/control API;
- device registry CLI, YAML and SQLite synchronization;
- MemoryGate persistence;
- audit receipt retention;
- health aggregation and recovery policy.

### Media

- media PC reachability and session state;
- Android/Waydroid runtime;
- mode controller;
- volume policy;
- browser/player/cast routes;
- `/remote` served asset and APIs;
- touchpad, keyboard and universal gamepad;
- USB HID/game controller visibility;
- foreground/application and media-session verification.

### MFP/document intake

- Brother device identity and scanner service;
- panel Scan-to-PC route;
- scan-key watchdog;
- automatic ADF/flatbed selection;
- multipage/duplex session assembly;
- OCR/inference worker;
- document classification and family routing;
- verified archive before mutation;
- Google Drive upload/outbox;
- canonical MemoryGateway write/readback;
- calendar-event projection;
- retry, quarantine and restart recovery.

### Local AI and memory projections

- Ollama service and allowlisted installed model availability, names kept private in public receipt;
- inference worker single-instance/restart state;
- Cognee/MemPalace/Graphify freshness against canonical revision;
- no direct canonical SQLite bypass;
- Video Understanding worker/provider/artifact/queue state;
- canonical memory root and exact readback.

### Network

Read-only only:

- ASUS gateway, Huawei modem and downstream MikroTik stable identities;
- topology and active uplink;
- link monitoring/recovery ownership;
- configuration backup availability;
- DNS/DHCP/firewall/Wi-Fi configuration drift indicators;
- modem active-state probe correctness;
- absence of competing DHCP/NAT/control ownership.

Do not expose credentials or private addresses and do not modify network configuration.

### IoT and physical devices

- LavaLamp and other registered ESP/WLED devices;
- Home Assistant/MQTT/Node-RED routes represented in the registry where applicable;
- operation idempotency and independent light/device-state verification;
- firmware/source identity and OTA rollback readiness;
- offline-device recovery and alert policy.

### Automation runtime

- Scheduler service/timer and exact release identity;
- durable schedule registry and occurrence ledger integrity;
- recurring home jobs represented as schedules or event-driven services;
- misfire/overlap/restart behavior;
- Runner maintenance routes needed for Home Edge;
- stale issue/worktree/branch deployment risk.

## Phase 3 — bounded performance checks

Perform only harmless read requests and registered green operations that cannot alter durable mode or configuration.

Measure separately:

- request dispatch latency;
- executor acceptance latency;
- application of the action;
- independent physical/application verification latency.

Engineering targets for routine local controls:

- dispatch/acceptance normally below 500 ms;
- applied state normally below 2 seconds;
- physical/application verification normally below 3 seconds.

These are target budgets, not reasons to hide slower measured results. Report p50/p95 where repeated harmless checks are available. Never stress-test devices or generate rapid repeated inputs.

## Phase 4 — autonomy-gap report

For every function record:

- subsystem/function ID;
- canonical device/service/operation ID;
- source implementation status;
- installed/enabled/running status;
- trigger type: event, timer, API, manual;
- restart persistence;
- idempotency;
- retry/backoff/quarantine;
- self-repair policy;
- failure alerting;
- verification level: sent, accepted, applied, physically verified;
- typical latency;
- last success/failure;
- backup/rollback;
- blocker and next safe action.

Generate a prioritized repair queue:

- P0: normal home function unavailable, data-loss risk, canonical-state split or unsafe bypass;
- P1: manual dependency, no restart recovery, no watchdog, no physical verification or unacceptable latency;
- P2: stale metadata, missing documentation, obsolete issue/PR or optimization.

## Persistence

Immediately persist verified outcomes to the proper stores:

- stable identities/capabilities/paths/operations → device registry;
- live state/services/history → state database;
- semantic conclusions, failures, topology decisions and next actions → embodied MemoryGate;
- execution evidence → Skeleton audit receipt.

Read-only audit findings may update audit metadata only through an already registered safe operation. If no such operation exists, report the missing operation rather than writing through an ad hoc path.

## Forbidden in this audit

- direct/manual SSH;
- second executor;
- arbitrary shell/path/host/URL parameters;
- broad network scan;
- package installation;
- router/modem/firewall/DHCP/DNS/Wi-Fi mutation;
- credentials/pairing changes;
- firmware/OTA;
- reboot, factory reset or destructive cleanup;
- starting/enabling/restarting services;
- input injection except one explicitly registered harmless green smoke;
- publishing secrets, addresses, usernames, paths or raw logs.

## Output

Return `DONE`, `PARTIAL` or `BLOCKED` with:

- audit coverage counts;
- subsystem state matrix;
- P0/P1/P2 gap counts and concise items;
- latency aggregates;
- runtime drift summary;
- registry/memory/state persistence status;
- audit receipt reference;
- exact next safe action for every P0;
- separate list of repairs requiring operator approval.
