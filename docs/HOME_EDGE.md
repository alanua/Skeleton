# Home Edge Universal Gateway

`home-edge-01` is the local media PC and the universal execution edge for the house.

The gate is universal by capability, not by accepting arbitrary shell text. Every remote operation is represented by a reviewed, typed action adapter with one of these risk lanes:

- `read_only`
- `approved_mutation`
- `destructive_manual`

Issue bodies and other external payloads may never provide the host, user, transport paths, command, or shell fragment.

## Production transport

Runner production uses ordinary OpenSSH over the fixed Tailscale IP:

```text
valertos08@100.127.35.74
```

The argv is built internally with batch mode, a single identity, strict host verification, a 10-second connection timeout and bounded keepalives. Runtime paths come only from these fixed Runner environment variables:

```text
SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE
SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE
```

Tailscale SSH remains an optional interactive adapter. It is not the Runner production default.

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
```

The existing baseline runtime maintenance id remains:

```text
home_edge_01_read_only_diagnostic
```

Additional house tasks are added as reviewed action adapters rather than raw shell supplied by a GitHub issue.

## Evidence model

Repository profile values are `registered`. Values returned by a successful remote probe are `observed`. Values that could not be probed are `unverified`. Registered values are never copied into observed fields.

## Two-stage deployment

Stage A merges the repository transport, evidence, action-registry, tests and documentation contract.

Stage B is a separately approved secure runtime bootstrap that configures dedicated Runner authentication, pins the host identity, installs the fixed service environment, validates one read-only probe from the real Runner service context and records only redacted observed evidence.

No runtime credentials or subscriber identifiers belong in GitHub artifacts.
