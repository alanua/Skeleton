# Skeleton bounded workstation agent — source-only prototype

**Status: not installed / not activated / no live CAD adapter.** This document describes a provider-neutral, deterministic offline simulation for the DIOS CAD pilot. PR review and one-time operator-approved activation are required before distributing any runtime to a Windows PC. See Skeleton #1720 and #4784, DIOS #31 / #36 and competing draft PRs #34 / #35.

## Authority and separation

1. A canonical Skeleton control-plane registry must attest a workstation node and bind installation ID to trusted device identity. No IP, hostname, GitHub label, or Desktop Commander connection can substitute for device enrollment.
2. A separate authenticated outbound connection (mTLS or equivalent via the registered Skeleton authority) establishes a `NodeSession`. A session refers to one node; an `ApplicationSession` refers to a **single workspace**, provider capability and context revision.
3. A separately approved `InteractionLease` authorizes either `OBSERVE` (only inspect, snapshot and proposal) or `APPLY_APPROVED` (the narrowly requested operations). Every request independently needs an exact `TrustedGate` authorization. The bound grant identifies node, session, lease, command, operation, and an unexpired approval; a missing, revoked, expired, mismatched or unavailable Gate fails closed.
4. Command expiry, replay/idempotency and current context revision are checked before any adapter call. A local pause or abort blocks future requests. Offline or stale sessions never acquire new authority. Already completed redacted receipts remain in a local volatile queue for future control-plane acknowledgement; no private payload enters public receipts.
5. Only preconfigured bounded adapters can execute an operation. Request payloads cannot choose an executable, path, transport, macro, browser, environment, shell or action adapter. The included `SyntheticWorkstationAdapter` uses only in-memory objects. Its apply/rollback functions **never invoke AutoCAD, SOLIDWORKS, the Windows registry, or a real file**.

The `GateAuthorization` dataclass is a **receipt from a trusted caller**, not cryptographic proof. The pure-Python component has no networking or secret storage. It is unsafe to expose this class directly to untrusted clients without the separately implemented canonical authenticated authority and registered Gate adapter. Existing protected Skeleton Gate sources must not be modified by this change.

## API shape and privacy

- Input: `schemas/workstation_command.schema.json` and `CommandEnvelope` (command ID, idempotency key, node/session/lease/workspace binding, operation, issued/expiry time, expected revision, bounded payload).
- Simulator operations: `INSPECT`, `SNAPSHOT`, `PROPOSE` (no mutation), `APPLY`, `CHECKPOINT`, `ROLLBACK` (in-memory only). `APPLY` accepts `entity_id` + `properties` only; rollback requires an existing checkpoint. No arbitrary shell/network access.
- Output: `CommandReceipt.public()` contains only safe `status`, `reason_class`, operation, revisions and a digest; no device hostname, client drawings, vendor license state, paths, provider data, access token or source CAD data.
- The volatile outbox is **not** durable, signed or encrypted; production must use a registered private encrypted, bounded-at-rest queue with authenticated server acknowledgements. The simulator intentionally does not implement transport, persistence or a deployable background service.

## Windows pilot preflight — non-mutating rehearsal

Run read-only checks on each device from the **authorized** Remote Desktop Commander surface. Keep raw device inventory private. Required: Windows + Python 3.11 or newer, accessibility of the authorized agent transport, CAD installed with licensing verified by the owner, capacity to run a per-user agent without elevated rights, private/local artifact boundary and explicit operator-controlled pause/abort.

Example PowerShell read-only commands:

```powershell
$PSVersionTable.PSVersion.ToString()
py -3 --version
(Get-Command git -ErrorAction SilentlyContinue) -ne $null
```

Do not install Python, CAD vendors, Windows services, scheduled tasks, SSH, firewall rules or extensions as part of this rehearsal. Do not query private drawings or transmit installed-app inventory to GitHub. No live launch or remote mutation is authorized by this PR.

## Required next slices (not implemented here)

1. Review/merge canonical DIOS CAD contract after resolving overlapping PR #34/#35. Canonical `main` must be verified, not inferred from green unit tests.
2. Implement the **private** Windows agent packaging and authenticated outbound session binding using the existing Skeleton registry, separate Gate admission and local pause/abort; add durable encrypted receipt queue and explicit rollback.
3. Implement one private provider-specific CAD adapter at a time and test `OBSERVE` on a disposable synthetic drawing/session. There is no generic way to safely mutate arbitrary CAD applications without approved per-provider APIs.
4. Only then request exact devices, adapter versions, allowed actions, validation and rollback authority for each workstation's separate deployment. No autosync/OTA/install/merge is implied by green tests.

Validation:

```bash
python -m pytest -q tests/test_workstation_agent.py
python -m py_compile core/workstation_agent.py adapters/workstation_control.py
```
