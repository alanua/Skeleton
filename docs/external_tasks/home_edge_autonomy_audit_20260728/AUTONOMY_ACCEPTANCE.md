# AUTONOMY ACCEPTANCE CONTRACT

A subsystem is not accepted as autonomous merely because code exists, tests pass, a package is installed, a unit is active or an API returns success.

## Required state for `READY_AUTONOMOUS`

Every routine function must satisfy all applicable checks:

- [ ] one canonical device/service identity;
- [ ] one registered reusable operation or event-driven worker;
- [ ] fixed bounded inputs; no arbitrary shell/host/path/key injection;
- [ ] enabled and restart-persistent where it must run continuously;
- [ ] single-instance or intentionally coordinated concurrency;
- [ ] idempotent request/replay handling;
- [ ] bounded timeout and retry/backoff;
- [ ] crash recovery and stale-claim recovery;
- [ ] quarantine or review path for irrecoverable input;
- [ ] self-repair only through registered safe recovery operations;
- [ ] alert only after bounded automatic recovery fails;
- [ ] independent verification of application or physical state;
- [ ] last-success and last-failure retained;
- [ ] backup and verified rollback where state/configuration can change;
- [ ] live state synchronized to registry/state database;
- [ ] durable semantic conclusion stored in MemoryGate;
- [ ] audit receipt retained;
- [ ] normal latency measured and within the appropriate budget, or a documented device-specific exception exists.

## Instant-operation target

Routine local control should not require conversational orchestration. A registered operation must be callable immediately from the owning local service or UI.

Target budgets:

- local dispatch/acceptance: p95 ≤ 500 ms;
- normal applied state: p95 ≤ 2 s;
- independent physical/application verification: p95 ≤ 3 s.

Long-running tasks such as OCR, media analysis, backups and firmware are exempt from the applied-state budget, but their enqueue acknowledgement must still be immediate and their progress/recovery must be autonomous.

## Failure behavior

Normal transient failures must be handled without user intervention when recovery is safe and deterministic:

1. detect;
2. read prior failure history;
3. invalidate stale assumptions;
4. perform one bounded registered recovery;
5. verify independently;
6. update state and memory;
7. alert the operator only when recovery fails, ambiguity remains or approval is required.

The system must never repeat the same failed guess indefinitely.

## Human-gated exceptions

Intervention remains mandatory for:

- new device pairing or credentials;
- router/modem/firewall/DHCP/DNS/VLAN/external exposure changes;
- firmware and OTA unless an exact previously approved operation exists;
- factory reset, destructive deletion or irreversible action;
- identity ambiguity;
- safety/security policy conflict;
- repeated failed recovery or missing physical verification.

## Audit completeness

The full audit is accepted only if:

- [ ] every confirmed registry device was inspected;
- [ ] every registered operation was classified;
- [ ] every required service/timer/worker/watchdog was inspected;
- [ ] every queue/outbox was checked for age and stuck work;
- [ ] all identity-pending and stale-state records were listed;
- [ ] runtime hashes were compared with approved releases;
- [ ] open Home Edge issues/PRs were reconciled against live state;
- [ ] every P0 has one exact next safe action;
- [ ] public output contains no private values;
- [ ] findings were persisted to canonical stores or a missing persistence operation was explicitly reported.
