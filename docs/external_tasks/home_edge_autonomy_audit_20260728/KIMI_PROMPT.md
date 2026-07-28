# COPY-PASTE PROMPT FOR KIMI

Audit the Home Edge autonomy architecture in the public repository `alanua/Skeleton`.

Read this packet first:

1. https://github.com/alanua/Skeleton/blob/external/home-edge-autonomy-audit-20260728/docs/external_tasks/home_edge_autonomy_audit_20260728/README.md
2. https://github.com/alanua/Skeleton/blob/external/home-edge-autonomy-audit-20260728/docs/external_tasks/home_edge_autonomy_audit_20260728/PRELIMINARY_FINDINGS.md
3. https://github.com/alanua/Skeleton/blob/external/home-edge-autonomy-audit-20260728/docs/external_tasks/home_edge_autonomy_audit_20260728/LIVE_AUDIT_TASK.md
4. https://github.com/alanua/Skeleton/blob/external/home-edge-autonomy-audit-20260728/docs/external_tasks/home_edge_autonomy_audit_20260728/AUTONOMY_ACCEPTANCE.md

Your task is repository/control-plane analysis, not private live execution.

Map every Home Edge-related module, installer, service/timer, worker, watchdog, queue/outbox, registry operation, maintenance route, test, document and currently open issue/PR. Determine where the implementation stops at one of these incomplete states:

- contract/reference only;
- source merged but not integrated;
- installer exists but does not enable/start;
- deployed once but not durable;
- active but not self-healing;
- health checked but not physically verified;
- manual-only;
- blocked by canonical memory, credentials or approval;
- superseded/stale work still open;
- missing entirely.

Cover at least:

- canonical Home Edge executor and audit route;
- device registry/state database/MemoryGate synchronization;
- universal remote/touchpad/keyboard/gamepad and media controls;
- Brother MFP panel scanning, ADF/flatbed, session assembly, OCR, Drive archive and MemoryGateway projection;
- Ollama/local inference and semantic projections;
- Video Understanding;
- Scheduler/Runner integration;
- ASUS/Huawei/MikroTik network control and monitoring boundaries;
- LavaLamp/ESP/WLED and other IoT devices;
- watchdogs, restart recovery, backups, rollback, latency and alerting.

Produce:

1. a repository architecture graph from intent to physical verification;
2. a subsystem readiness matrix using the exact states in `LIVE_AUDIT_TASK.md`;
3. a list of missing generic capabilities, especially a bounded full-node audit operation and an autonomy supervisor;
4. a reconciliation table for relevant open issues/PRs: active, superseded, stale, duplicate or still blocking;
5. minimal repository patches or PRs for green/public-safe gaps;
6. exact specifications for private/live tasks that Skeleton must execute later through `Skeleton_Home_Edge.home_edge_exec` on `home-edge-01`;
7. tests proving that merged/source/installed/active/applied/physically_verified are never conflated.

Do not request credentials, private paths, private addresses or direct Home Edge access. Do not propose direct SSH, a second executor, broad scans or arbitrary shell/path/host parameters. Do not declare live health from GitHub evidence.

Open one PR against `alanua/Skeleton:main` if repository changes are justified. Return your analysis as a comment on the linked GitHub task issue and begin with exactly `RESULT: DONE` or `RESULT: BLOCKED`.
