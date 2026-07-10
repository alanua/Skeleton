# Home Edge ESP Lab

ESP Lab remains controlled by one Home Edge/Skeleton control plane. Today that
control plane is the media PC, but connector jobs address logical node and
adapter identifiers so the Home Edge machine can change without changing the job
contract.

Execution endpoints are subordinate:

- `home_edge_local_linux` uses local `/dev/ttyUSB*` and `/dev/ttyACM*`.
- `windows_workstation_connector` uses desk-local `COM1` through `COM256`.

Home Edge explicitly selects `node_id`, `endpoint_kind`, and `adapter_kind` for
each job. There is no automatic device failover between endpoints. Policy,
approval, audit, node registration, and public receipt aggregation stay in Home
Edge/Skeleton. Endpoints only perform typed allowlisted local work and return a
private normalized observation plus a public-safe receipt.

Supported operations:

- `discover_serial_candidates`: Linux reads a supplied sysfs root; Windows reads
  `HARDWARE\DEVICEMAP\SERIALCOMM` through an injected registry adapter. Discovery
  never opens a serial port.
- `identify_chip`: plans or executes only `esptool --port <device> read-mac`.
- `inspect_flash_identity`: plans or executes only
  `esptool --port <device> flash-id`.
- `observe_serial_bounded`: uses an injected serial adapter for bounded receive
  only; no bytes are transmitted.

The Windows connector exposes only:

- `GET /v1/esp-lab/health`
- `POST /v1/esp-lab/jobs`

The default bind is loopback and plan-only. LAN bind requires an explicit LAN
flag, TLS certificate/key, shared-secret file, and an allowlisted logical node
id. Home Edge validates TLS with a supplied CA certificate or an exact pinned
SHA-256 certificate fingerprint; certificate verification is never disabled.

Application authentication uses HMAC-SHA256 over connector version, method,
path, timestamp, nonce, idempotency key, and exact body hash. The connector
rejects stale timestamps, nonce replay, idempotency body mismatch, invalid
signature, unknown node, oversized bodies, unknown fields, and wrong endpoint
jobs before adapter execution.

The public receipt never includes COM or tty paths, MAC addresses, USB serials,
VID/PID pairs, hostnames, usernames, IP addresses, certificate paths, secret
paths, private artifact paths, or local topology. Those details stay private and
bounded in the observation, with device identity represented by a salted
fingerprint bound to node id and adapter kind.

CLI examples:

```bash
python3 scripts/home_edge_esp_lab.py discover --sysfs-root tests/fixtures/sysfs
python3 scripts/home_edge_esp_lab.py validate-job --job /tmp/esp-job.json
python3 scripts/home_edge_esp_lab.py plan --job /tmp/esp-job.json
python3 scripts/home_edge_esp_lab.py inspect --job /tmp/esp-job.json --private-out /tmp/private.json --receipt-out /tmp/receipt.json
python3 scripts/home_edge_esp_lab_windows_connector.py validate-config --node-id desk-win --secret-file /path/to/secret
python3 scripts/home_edge_esp_lab_windows_connector.py capabilities --node-id desk-win --secret-file /path/to/secret
python3 scripts/home_edge_esp_lab_windows_connector.py serve --node-id desk-win --secret-file /path/to/secret
```

`serve` defaults to loopback and plan-only. LAN and read-only execution require
separate explicit flags. This PR does not deploy, install, register a service,
change firewall rules, generate certificates, or touch real Home Edge, LAN,
Windows registry, USB, or serial devices.
