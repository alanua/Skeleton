# Home Edge Universal Gateway

`home-edge-01` is the synthetic public name for the private local execution edge.

The gate is universal by capability, not by accepting arbitrary shell text. Every remote operation is represented by a reviewed, typed action adapter with one of these risk lanes:

- `read_only`
- `approved_mutation`
- `destructive_manual`

Issue bodies and other external payloads may never provide the host, user, transport paths, command, or shell fragment.

## Production transport

Runner production uses ordinary OpenSSH over a private Tailscale address:

```text
private-runner-user@private-home-edge-address
```

The argv is built internally with batch mode, a single identity, strict host verification, a 10-second connection timeout and bounded keepalives. Runtime credential paths come only from these fixed Runner environment variables:

```text
SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE
SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE
```

Tailscale SSH remains an optional interactive adapter. It is not the Runner production default.

Runtime diagnostic artifacts are private by default. The CLI runs without persistence unless
an artifact path is explicitly supplied. Every profile source other than
`synthetic_template` is rejected if the artifact path resolves to the repository root or any
descendant, even when local profile or environment override values match the public template.
The public checkout may only contain synthetic templates and aggregate statuses.

## Audited actions

```bash
python3 scripts/home_edge_remote.py gateway_capabilities
python3 scripts/home_edge_remote.py diagnostic --operator-report
python3 scripts/home_edge_remote.py system_inventory
python3 scripts/home_edge_remote.py network_inventory
python3 scripts/home_edge_remote.py service_inventory
python3 scripts/home_edge_remote.py container_inventory
python3 scripts/home_edge_remote.py media_inventory
python3 scripts/home_edge_remote.py browser_diagnostic
python3 scripts/home_edge_remote.py hardware_inventory
python3 scripts/home_edge_remote.py home_automation_inventory
python3 scripts/home_edge_remote.py video_visual_capture
```

The existing baseline runtime maintenance id remains:

```text
home_edge_01_read_only_diagnostic
```

The visual capture queue tick is also a typed action:

```text
home_edge_01_video_visual_capture_tick
```

Additional house tasks are added as reviewed action adapters rather than raw shell supplied by a GitHub issue.

## Evidence model

Repository profile values are synthetic `registered` placeholders. Values returned by a
successful remote probe are `observed`. Values that could not be probed are `unverified`.
Registered values are never copied into observed fields. Hostnames, controller identity, SSH
users, Tailscale or LAN addresses, gateway or interface values, paths, credentials,
subscriber identifiers and raw host inventory remain local/private and are not reported to
GitHub.

## Two-stage deployment

Stage A merges the repository transport, evidence, action-registry, tests and documentation contract.

Stage B is a separately approved secure runtime bootstrap that configures dedicated Runner authentication, pins the host identity, installs the fixed service environment, validates one read-only probe from the real Runner service context and records only redacted observed evidence.

No runtime credentials or subscriber identifiers belong in GitHub artifacts.

## Explicit LAN inventory

The normal Home Edge diagnostic stays lightweight and does not sweep the local network. A
separate audited action, `lan_inventory`, is available only through the explicit maintenance
task `home_edge_01_lan_inventory_read_only`.

The action derives the target from the observed default-route source address on the primary
private IPv4 interface, refuses networks larger than `/24`, and filters neighbor-table entries
to that same network. It performs at most one ICMP check per address when `ping` is available
and uses only a fixed code-defined set of TCP connect checks. It performs no authentication,
banner collection, vulnerability testing, configuration change, package installation or
issue-controlled command/port execution.

Detailed IP, MAC and per-host service records remain in the configured private runtime
artifact, written with owner-only permissions on POSIX hosts. GitHub output contains aggregate
counts, service-category counts, gateway presence and bounded risk flags only.

An attached USB modem is optional and is never part of the Home Edge health criterion. The
registered internet-path expectation is the default gateway with integrated connectivity
hardware; gateway modem internals are not claimed as observed by Home Edge.

## Visual Capture Boundary

`video_visual_capture` is a private Home Edge execution capability. Skeleton owns the job
contract, queue tick, policy checks, audit metadata and public-safe receipt. Home Edge owns
browser and media execution through fixed private runtime configuration.

Job input may identify only the provider, normal YouTube watch URL, requested timestamp,
bounded offsets, capture mode and public action/task references. It may not provide shell
commands, selectors, executable paths, output paths, browser profile paths, hosts, users or
ports.

Screenshot frames, clips, contact sheets and the private manifest are written only under the
private artifact root. Public output is limited to the versioned receipt fields documented in
`docs/HOME_EDGE_VISUAL_CAPTURE.md`. Canonical memory may later store only reviewed
manifests, hashes and evidence state. Graphify and MemPalace remain derived indexes and are
not authority for capture evidence.
