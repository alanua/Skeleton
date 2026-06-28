# Home Edge 01

`home-edge-01` is the universal local node controlled by the Hetzner Skeleton Runner over the existing Tailscale connection.

Profile: `config/home_edge/home-edge-01.json`

Audited route:

```bash
python3 scripts/home_edge_remote.py diagnostic --operator-report
python3 scripts/home_edge_remote.py identity
python3 scripts/home_edge_remote.py tool_inventory
python3 scripts/home_edge_remote.py modem_diagnostic
python3 scripts/home_edge_remote.py prepare_private_unlock_plan
```

The diagnostic route is read-only. It uses `tailscale ssh valertos08@home-edge-01 python3 -` and writes a public-safe JSON artifact to `docs/home_edge/home-edge-01-diagnostic.latest.json`.

Safety rules:

- Keep the default route on `enp1s0` through `192.168.1.1` unchanged until a separate gateway migration is approved.
- Treat the Huawei E3372 as a ModemManager/NCM modem. Ignore the failed generic Ethernet profile `huawei-diag`; do not use it as the connection path.
- Do not print or store SIM PIN, passwords, tokens, IMEI, IMSI, ICCID, or private credentials in GitHub artifacts.
- Do not flash modem firmware, erase NVRAM, unlock bootloaders, alter IMEI, or expose management APIs publicly.

Prepared next action, not executed:

1. Unlock the SIM only through an operator-approved private secret route.
2. Configure the O2 APN through ModemManager after unlock.
3. Test antenna placement and signal fields before any gateway migration.
4. Plan the later MikroTik migration with rollback evidence.
