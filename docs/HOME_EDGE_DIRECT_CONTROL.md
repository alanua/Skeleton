# Home Edge Direct Control

`home-edge-01` exposes a reviewed direct action gateway for routine controls that should
complete during the same ChatGPT/Jeeves interaction. It is not a command executor. Every
tool is a narrow typed action with a fixed parameter schema, HMAC authentication, nonce
replay protection and idempotent receipts.

## Actions

- `media.get_volume`
- `media.set_volume` with `level` 0..100 and `target` `host`, `android_tv` or `both`
- `media.mute` and `media.unmute`
- `media.playback_status`
- `home_edge.health`
- `home_edge.diagnostic`
- `tv.get_mode`
- `tv.set_mode` with `mode` `chrome`, `waydroid`, `vlc`, `kiosk` or `off`

`tv.set_mode` to `off` requires `confirm_off: true`. Higher-risk actions remain outside
the direct gateway and require separate approval.

## Runtime Route

The gateway reuses the existing Home Edge strict SSH profile:

```text
node_id=home-edge-01
transport=openssh_over_tailscale_ip
identity_env=SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE
known_hosts_env=SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE
```

The local server sends a fixed Python action adapter over `ssh ... python3 -`. Request
parameters never provide a shell fragment, executable path, username, host, SSH option or
arbitrary subprocess argv. The remote adapter selects only code-defined argv lists for
`wpctl`, bounded `pactl` fallback, and Waydroid media commands.

## Request and Receipt

Requests use `schemas/home_edge_action_request.schema.json`. Required fields:

```json
{
  "node_id": "home-edge-01",
  "action_id": "media.set_volume",
  "request_id": "req-...",
  "timestamp": "2026-07-11T12:00:00+00:00",
  "nonce": "base64url-or-random-token",
  "idempotency_key": "stable-key-for-this-user-request",
  "parameters": {
    "level": 100,
    "target": "both"
  }
}
```

Receipts use `schemas/home_edge_action_receipt.schema.json` and include the original action
id, request id, timestamp, nonce, idempotency key, verified status and sanitized result. A
retry with the same idempotency key and identical request body returns the cached first
receipt and does not repeat a mutation. Reusing the key with a different body is rejected.

Receipts and errors redact usernames, hostnames, IP addresses, paths, account data, media
titles, URLs and raw command output. Mutations report `unverified`, `unsupported` or
`partial_failure` instead of claiming success without read-back verification.

## Private Connector Setup

Deployment is a separate operator-approved runtime step. Do not expose the endpoint on the
public Internet.

1. Create protected runtime files or environment variables outside the repository:

```bash
export SKELETON_HOME_EDGE_ACTION_KEY_ID='operator-key-id'
export SKELETON_HOME_EDGE_ACTION_HMAC_SECRET_FILE='/run/secrets/home-edge-action-hmac'
export SKELETON_HOME_EDGE_01_PROFILE='/run/skeleton/home-edge-01.json'
export SKELETON_HOME_EDGE_01_SSH_IDENTITY_FILE='/run/secrets/home-edge-01-ssh'
export SKELETON_HOME_EDGE_01_SSH_KNOWN_HOSTS_FILE='/run/secrets/home-edge-01-known-hosts'
```

2. Bind the server only to loopback or a private Tailscale address:

```bash
python3 scripts/home_edge_action_server.py --bind 127.0.0.1 --port 8765
```

3. Configure ChatGPT/Jeeves to call the private URL through Tailscale. Use HMAC SHA-256 over
the exact request body and send:

```text
x-home-edge-key-id: operator-key-id
x-home-edge-signature: sha256=<hex digest>
```

4. Use `/mcp/tools/list` after authentication to discover stable narrow tool names. Use
`/mcp/call` or `/actions` to submit the typed request body.

Unauthenticated requests receive only `authentication_required`; they do not receive node
identity, topology or action details.

## Manual Pilot Plan

Deploy only after separate explicit approval.

1. Connect to the endpoint privately through Tailscale.
2. Call `home_edge.health`.
3. Call `media.get_volume`.
4. While YouTube is playing in Waydroid Android TV, call `media.set_volume` with target
   `both` and level `100`.
5. Verify host and Android read-back without interrupting playback.
6. Repeat the exact same request body and confirm the cached idempotent receipt.
7. Retain the private receipt locally and return only the sanitized status.
