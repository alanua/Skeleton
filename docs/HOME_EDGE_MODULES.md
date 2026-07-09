# Home Edge v1 Module Inventory

This is the public-safe current-state inventory for Home Edge v1 modules.
It is documentation only. It does not implement, deploy, enable or operate
any Home Edge module.

## Status Vocabulary

| Status | Meaning |
| --- | --- |
| `merged_live_contract` | Contract, code or documentation is merged on `main`; any runtime use still requires the existing typed action and private operator environment. |
| `implemented_unmerged` | Implementation exists only in an open or draft PR and must not be treated as live. |
| `blocked_repair` | Work is present but explicitly blocked by review, issue state or a repair task. |
| `architecture_only` | Public architecture or target behavior is recorded, but no merged implementation contract exists. |
| `backlog` | Desired capability with no current implementation packet. |
| `needs_operator` | Requires private runtime approval, hardware access or local environment setup before any safe observation can occur. |

## Evidence Snapshot

Public GitHub evidence checked on 2026-07-08:

| Item | Public state | Evidence used |
| --- | --- | --- |
| Issue #1505 | Open architecture packet | Defines Hetzner as orchestration brain, Home Edge as LAN-side hands, seven capability domains, Device Registry authority and scanner-input-only rule. |
| Issue #1310 | Open, `runner:blocked`, `risk:yellow` | Modem probe replacement must preserve exact UUID ownership, active-state validation, aggregate-only output and no runtime execution. |
| Issue #1549 | Open, `risk:yellow` | Visual-capture deployment gate is approved only after merged implementation; requires private preflight, private spool/artifact/profile setup and sanitized aggregate receipts. |
| PR #1638 | Open draft, unmerged | Draft PR titled "Repair Home Edge visual capture blockers"; head `1f6c0463682f7c36a8b3f3e44182571d8323ee19`; review comment says `DO_NOT_MERGE` and requests repair. |
| Issue #1640 | Open, `runner:blocked`, `risk:yellow` | Repair task for PR #1638 only; forbids new PR, merge, runtime Home Edge access, package install, profile creation or service mutation. |
| Current `main` | Merged contract only | Contains `core/home_edge/profile.py`, `core/home_edge/diagnostics.py`, `core/home_edge_v1.py`, `scripts/home_edge_remote.py`, `docs/HOME_EDGE.md` and `docs/HOME_EDGE_V1.md`. |

The requested `docs/HOME_EDGE_VISUAL_CAPTURE.md` path is not present on current
`main`. Visual-capture evidence is therefore taken from public issue and PR
metadata, not from a merged documentation file.

## Operating Principles

- Hetzner/Skeleton remains the orchestration brain: planning, queue, memory,
  review and policy stay off the LAN-side node.
- Home Edge remains a portable execution role inside the LAN. The current
  public carrier name is `home-edge-01`, but task logic must route by
  capability and registry entry rather than by a hardcoded machine.
- Device Registry is the capability source of truth. Scanners, probes and
  inventories are update inputs only; they do not become authority by
  discovering a device.
- Safe read-only inventory and diagnostics come before mutation. Device
  control, provisioning, modem activation, service changes, browser/profile
  setup and deployment remain gated by explicit operator approval.
- Public artifacts may contain only synthetic identity, aggregate status,
  counts, stable reason codes and reviewed hashes. They must not contain private
  hostnames, addresses, users, credentials, browser profile paths, runtime paths,
  device identifiers, video identifiers, screenshots or raw probe output.

## Domain Matrix

| Domain | Current classification | Mapped modules | Current public contract | Dependencies and blockers |
| --- | --- | --- | --- | --- |
| Connectivity | `merged_live_contract` for profile, transport contract, diagnostics and typed command gate; `needs_operator` for observed runtime state | Node/profile registry, diagnostics, typed remote action transport, default-route/Tailscale summaries | `HomeEdgeProfile` provides a synthetic public profile with fixed node id and OpenSSH-over-private-network contract. `run_audited_home_edge_command` exposes allowlisted actions. Diagnostics return registered, observed or unverified evidence without copying private values into public output. | Runtime observation requires private Runner SSH identity and strict host-key environment. No issue payload may supply host, user, paths, command or shell text. |
| Device Control | `architecture_only` for Home Assistant/MQTT service-call capability; `backlog` for mutation adapters | Home Assistant/MQTT control, smart lights, ESP/WLED-style devices | `core/home_edge_v1.py` registers `ha.service_call` under the Device Control domain and routes it only through dry-run executor behavior. | Real control needs a reviewed adapter, Device Registry target resolution, operator approval for mutation and tests proving no scanner-sourced authority escalation. |
| Network Admin | `merged_live_contract` for read-only LAN inventory and aggregate modem summaries; `blocked_repair` for modem activation/probe chain | Modem/network probe, LAN inventory, route and hardware summaries | `lan_inventory` is an explicit audited action with private detailed artifact and public aggregate counts only. `modem_diagnostic` summarizes optional attached USB modem state and keeps gateway modem internals as not observed by Home Edge. | Issue #1310 blocks modem-chain mutation/probe repair until exact connection ownership and active-state validation are fixed. Live LAN scan requires explicit maintenance task and private artifact path outside the public checkout. |
| Service Hub | `merged_live_contract` for generic read-only service/container/tool projections; `architecture_only` for local files/printer modules | Local services, files, printer, containers, tool inventory | Diagnostics support `service_inventory`, `container_inventory` and `tool_inventory` projections from the remote diagnostic artifact. `core/home_edge_v1.py` registers `service.file_status` as a dry-run capability. | File-share, printer and local service health details need bounded read-only module specs and tests before any adapter is added. No package, service or filesystem mutation is authorized from this inventory. |
| Human Interface | `implemented_unmerged` and `blocked_repair` for visual capture; `architecture_only` for kiosk/photo-frame devices | Visual capture, kiosk/photo-frame devices, display/control-panel surfaces | PR #1638 contains an unmerged visual-capture implementation with typed remote action, private artifacts and sanitized public receipts. The v1 registry has `ui.display` dry-run capability. | PR #1638 is draft and blocked by review; Issue #1640 requires repair of Runner environment isolation, browser profile use, visible-kiosk behavior, media fallback seek and subprocess failure checks before merge. Issue #1549 runtime deployment may only run after a repaired implementation is merged and validated. |
| Provisioning and Recovery | `merged_live_contract` for prepared-not-executed bootstrap receipt and dry-run provisioning capability; `architecture_only` for full recovery modules | Provisioning/recovery, runtime bootstrap, rollback evidence | `prepare_runtime_bootstrap` returns `prepared_not_executed`, `allowed_runtime_mutation=false`. `core/home_edge_v1.py` registers `device.provision_dry_run`. Docs require two-stage deployment and private runtime evidence. | Any real provisioning, recovery or rollback work requires private operator approval, exact identity checks, route and Tailscale recovery verification and rollback evidence. |
| Media and Presence | `merged_live_contract` for generic media inventory and dry-run `media.play`; `architecture_only` for LMS/cast/presence; `backlog` for presence sensors | LMS/media/cast, media inventory, presence, Google Nest/TV/cast-style playback | Diagnostics include `media_inventory`. `core/home_edge_v1.py` registers `logitech_media_server` and `media.play` as dry-run registry entries. | LMS/cast control and presence must start with read-only discovery/status modules. Playback, cast routing and occupancy-derived automation are mutation or privacy-sensitive and need explicit approval. |

## Concrete Module Inventory

| Module | Classification | Current evidence | Notes |
| --- | --- | --- | --- |
| Node/profile registry | `merged_live_contract` | `core/home_edge/profile.py`; `docs/HOME_EDGE.md`; Issue #1505 | Synthetic public profile, fixed node id and environment override boundary are merged. Private runtime profile values remain outside GitHub. |
| Diagnostics | `merged_live_contract` | `core/home_edge/diagnostics.py`; `tests/test_home_edge_diagnostics.py` | Read-only public artifact builder, aggregate summaries, redaction and no private artifact writes into the repo for runtime profiles. |
| Typed remote action transport | `merged_live_contract` | `scripts/home_edge_remote.py`; `core/home_edge/diagnostics.py` | CLI accepts only allowlisted action ids and uses internally built transport; no raw shell or issue-supplied transport fields. |
| Modem/network probe | `merged_live_contract` for optional summary; `blocked_repair` for active modem-chain workflow | `summarize_modem`; `modem_diagnostic`; Issue #1310 | USB modem is optional and not a Home Edge health criterion. The yellow modem workflow remains blocked until exact ownership and active-state validation are repaired. |
| Visual capture | `implemented_unmerged` plus `blocked_repair` | PR #1638; Issue #1640; Issue #1549 | Not live. Deployment gate must wait for repaired merge and then a separate private runtime task. |
| Home Assistant/MQTT control | `architecture_only` | Issue #1505; `ha.service_call` dry-run registry entry | Capability exists as architecture/dry-run registry only. No control adapter is merged. |
| Local services/files/printer | `architecture_only` with partial read-only service inventory contract | `service_inventory`, `container_inventory`, `tool_inventory`; `service.file_status` dry-run registry entry | Service/container inventory is merged; file/printer-specific modules remain planned. |
| LMS/media/cast | `architecture_only` with partial media inventory contract | `media_inventory`; `logitech_media_server` registry service; Issue #1505 | Media status can be projected read-only; cast/playback control is not implemented. |
| Kiosk/photo-frame devices | `architecture_only` | Issue #1505; `ui.display` dry-run registry entry | Treat as display targets registered by Device Registry, not as scanner-created authority. |
| Presence | `backlog` | Issue #1505 includes Media and Presence domain | No merged presence module or issue-specific implementation packet found in the inspected evidence. |
| Provisioning/recovery | `merged_live_contract` for prepared receipt and dry-run route; `needs_operator` for runtime action | `prepare_runtime_bootstrap`; `device.provision_dry_run`; `docs/HOME_EDGE.md` | Bootstrap is explicitly prepared-not-executed. Real recovery/provisioning is private, approved and rollback-bound. |

## Ordered Next Three Modules

These recommendations assume PR #1638 has first been repaired, validated and
merged. They intentionally keep read-only inventory ahead of mutation.

### 1. Registry-backed capability inventory receipt

Goal: emit a public-safe read-only inventory that joins Device Registry entries
with existing diagnostic projections and marks each capability as registered,
observed, unverified or blocked without promoting scanner output to authority.

Allowed-file sketch:

- `core/home_edge_v1.py`
- `core/home_edge/diagnostics.py`
- `scripts/home_edge_remote.py`
- `tests/test_home_edge_v1.py`
- `tests/test_home_edge_diagnostics.py`
- `docs/HOME_EDGE_MODULES.md`

Test gates:

- Registry remains the only source of capability truth.
- Scanner/probe data can update evidence state but cannot create authoritative
  capabilities.
- Public receipt contains only node id, capability names, domains, state and
  aggregate reason codes.
- Full focused Home Edge tests, `py_compile` for touched Python files and
  `git diff --check`.

Non-goals:

- No runtime scan.
- No Device Registry schema migration.
- No Home Assistant, MQTT, media, printer, browser or provisioning mutation.

### 2. Service Hub read-only local services/files/printer inventory

Goal: add bounded read-only inventory for local service health classes, file
service availability classes and printer presence classes, reported as aggregate
public status and private detailed runtime artifact only.

Allowed-file sketch:

- `core/home_edge/diagnostics.py`
- `scripts/home_edge_remote.py`
- `tests/test_home_edge_diagnostics.py`
- `tests/test_runner_poll_github_tasks.py`
- `docs/HOME_EDGE.md`
- `docs/HOME_EDGE_MODULES.md`

Test gates:

- Fixed code-defined probes only; no issue-supplied host, path, port, command or
  share name.
- Public output omits private service names, paths, printer identifiers and raw
  command output.
- Private artifact target must be outside the public checkout.
- Existing diagnostic and LAN inventory tests remain passing.

Non-goals:

- No file copy, backup, print job, service restart, package install or share
  configuration.
- No credential read.
- No scanner-sourced registry creation.

### 3. Media and presence read-only status inventory

Goal: add a non-mutating media/presence status receipt covering LMS/media server
availability, cast-style target classes, kiosk/photo-frame display reachability
classes and presence-source availability classes.

Allowed-file sketch:

- `core/home_edge_v1.py`
- `core/home_edge/diagnostics.py`
- `scripts/home_edge_remote.py`
- `tests/test_home_edge_v1.py`
- `tests/test_home_edge_diagnostics.py`
- `docs/HOME_EDGE_MODULES.md`

Test gates:

- Public output reports counts/classes only and no room names, person names,
  device ids, media titles, URLs, screenshots or playback state details.
- Registered `media.play` and `ui.display` capabilities remain dry-run only.
- Presence status is availability-only; no automation trigger is produced.
- Existing media inventory behavior remains backward compatible.

Non-goals:

- No playback, cast session creation, kiosk deployment, photo-frame enrollment,
  visual capture runtime action or presence-driven automation.
- No Home Assistant or MQTT mutation.

## Deferred Mutation Order

After the three read-only modules above, mutation-capable modules should proceed
only in this order and only with explicit operator approval:

1. Home Assistant/MQTT device control through registry-resolved typed service
   calls.
2. Media/cast playback commands with dry-run, explicit approval, verify and
   rollback/stop semantics.
3. Provisioning/recovery actions with exact identity, power, disk, route and
   Tailscale recovery evidence.
4. Modem-chain actions after Issue #1310 is repaired and reviewed.

Visual capture deployment remains separately gated by Issue #1549 after repaired
PR #1638 is merged. It is not a prerequisite for the read-only inventory modules
unless a later task explicitly makes it one.
