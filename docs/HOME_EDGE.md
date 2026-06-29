# Home Edge Universal Gateway

`home-edge-01` is represented in this repository by public-safe templates only.
Real hostnames, users, Tailscale addresses, LAN addresses, gateway addresses,
SSH material paths and controller identifiers stay outside tracked artifacts.

The gate is universal by capability, not by accepting arbitrary shell text. Every
remote operation is represented by a reviewed, typed action adapter with one of
these risk lanes:

- `read_only`
- `approved_mutation`
- `destructive_manual`

Issue bodies and other external payloads may never provide the host, user,
transport paths, command, or shell fragment.

## Production Transport

Runner production uses ordinary OpenSSH over the runtime-provided Tailscale
target. The tracked profile contains template values such as
`home-edge-user@100.64.0.10`; operators provide real values through an ignored
local profile path or explicit environment variables.

Runtime endpoint variables:

```text
SKELETON_HOME_EDGE_01_LOCAL_PROFILE
SKELETON_HOME_EDGE_01_NODE_ID
SKELETON_HOME_EDGE_01_HOSTNAME
SKELETON_HOME_EDGE_01_TAILSCALE_IP
SKELETON_HOME_EDGE_01_CONTROLLER_HOST
SKELETON_HOME_EDGE_01_CONTROLLER_TAILSCALE_IP
SKELETON_HOME_EDGE_01_TARGET_USER
SKELETON_HOME_EDGE_01_PRIMARY_INTERFACE
SKELETON_HOME_EDGE_01_PRIMARY_ADDRESS
SKELETON_HOME_EDGE_01_PRIMARY_GATEWAY
```

Runner SSH material variables:

```text
SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE
SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE
```

The argv is built internally with batch mode, a single identity, strict host
verification, a 10-second connection timeout and bounded keepalives. Tailscale
SSH remains an optional interactive adapter. It is not the Runner production
default.

## Audited Actions

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
```

The baseline runtime maintenance id is:

```text
home_edge_01_read_only_diagnostic
```

Additional house tasks are added as reviewed action adapters rather than raw
shell supplied by a GitHub issue.

## Evidence Model

Repository profile values are `template`. Values returned by a successful remote
probe are `observed`. Values that could not be probed are `unverified`. Template
values are never copied into observed fields.

## Two-Stage Deployment

Stage A merges the repository transport, evidence, action registry, tests and
documentation contract.

Stage B is a separately approved secure runtime bootstrap that configures
dedicated Runner authentication, pins the host identity, installs the fixed
service environment, validates one read-only probe from the real Runner service
context and records only redacted observed evidence.

No runtime credentials, private network endpoints or subscriber identifiers
belong in GitHub artifacts.
