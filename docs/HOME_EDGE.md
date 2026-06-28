# Home Edge Runner Control

Skeleton registers `home-edge-01` as a durable local universal node in `config/home_edge/home-edge-01.json`.

Runner command:

```bash
python3 scripts/home_edge_remote.py diagnostic --operator-report
```

Runtime maintenance task id:

```text
home_edge_01_read_only_diagnostic
```

The task is intentionally read-only. It verifies identity, Tailscale reachability, primary route preservation, tool inventory, and Huawei E3372 modem state. Private SIM unlock and O2 APN configuration are prepared as a next action, but are not executed from public GitHub task artifacts.
