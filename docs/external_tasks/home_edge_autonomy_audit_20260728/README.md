# Home Edge Autonomy Audit — 2026-07-28

This packet defines a complete audit of the physical Home Edge node and every registered home-network function.

## Operator requirement

All routine home processes must be autonomous, persistent, restart-safe, idempotent and fast. Normal operation must not depend on ChatGPT, manual SSH, one-off shell commands, temporary worktrees, issue comments or operator intervention. Human intervention is reserved for failures, unsafe ambiguity, credentials/pairing, destructive actions and changes requiring explicit approval.

## Read order

1. [PRELIMINARY_FINDINGS.md](./PRELIMINARY_FINDINGS.md)
2. [LIVE_AUDIT_TASK.md](./LIVE_AUDIT_TASK.md)
3. [AUTONOMY_ACCEPTANCE.md](./AUTONOMY_ACCEPTANCE.md)
4. [KIMI_PROMPT.md](./KIMI_PROMPT.md)

## Scope

- `home-edge-01` node health and boot persistence;
- canonical MemoryGate, device registry and state database;
- every registered device, service, path and operation;
- systemd services/timers, workers, watchdogs, queues and outboxes;
- media PC / Android / Waydroid / browser / players / cast;
- universal remote, touchpad, keyboard and gamepad;
- Brother MFP scanning, ADF/flatbed selection, scan button route, OCR, document assembly, Drive archive and MemoryGateway projection;
- local Ollama inference and semantic projection workers;
- router, modem and downstream network administration routes, read-only in this audit;
- LavaLamp and other ESP/IoT firmware/control paths;
- Scheduler, Runner and maintenance routes that affect Home Edge;
- backups, rollback, retry, recovery, alerting, audit receipts and stale state cleanup.

## Canonical execution boundary

No physical or runtime action may bypass `Skeleton_Home_Edge.home_edge_exec`. The live audit targets `node_id=home-edge-01` and uses exact bounded operations only. Direct SSH, a second executor and arbitrary command/path/host parameters are forbidden.

## Current limitation

The present ChatGPT runtime does not expose the Home Edge connector or mount the node's private MemoryGate/registry paths. Therefore repository/control-plane findings are recorded here, while the real node audit is defined as a read-only Skeleton maintenance task. A live state must not be guessed from GitHub state.
