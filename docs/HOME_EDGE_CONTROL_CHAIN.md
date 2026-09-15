# Home Edge natural-language control chain

## Verified base

The existing universal signed Home Edge executor remains the only execution
boundary. The controller-to-receipt path is already deployed and measured below
one second in the accepted smoke.

The end-user layer in this change is bounded. It does not accept shell, argv,
paths, hosts, environment variables, SSH parameters, or signing material.

## Registered media states

The media PC already has five trusted state transitions represented by GNOME
custom shortcuts:

| Mode | Existing shortcut | Meaning |
|---|---|---|
| `chrome` | `Super+Alt+1` | Chrome fullscreen |
| `android_tv` | `Super+Alt+2` | Waydroid / Android TV |
| `vlc` | `Super+Alt+3` | VLC |
| `kiosk` | `Super+Alt+4` | Chrome kiosk |
| `off` | `Super+Alt+0` | Media off |

There are four active display modes plus `off`. The fifth state is therefore
key `0`, not `Super+Alt+5`.

The bounded controller resolves the current commands from GNOME custom
keybindings on Home Edge and invokes only the command already registered for the
selected shortcut. Application launch logic is not duplicated in the AI-facing
layer.

## Natural-language operations

The bounded control accepts:

- one mode from the table above;
- an optional default audio volume from `0` to `100` percent;
- both in one request.

Examples:

- “Switch to Android TV and set volume to 80%.”
- “Open Chrome kiosk.”
- “Turn the media screen off.”
- “What mode and volume are active?”

The result contains a sanitized readback: selected state marker, active process
hint, resolved shortcut name, volume, mute state, duration, and receipt hash.
The underlying shortcut command is represented only by a hash.

## Transport A: private stdio MCP

Install on the trusted Hetzner controller:

```bash
sudo scripts/install_home_edge_media_control.sh \
  --repo-root /home/agent/agent-dev/repos/Skeleton
```

Installed command:

```text
/usr/local/bin/skeleton-home-media-control-mcp
```

The server exposes exactly two tools:

- `home_media_status` — read-only;
- `home_media_control` — bounded routine mutation.

Registration fragment:

```text
/etc/skeleton/mcp/skeleton-home-media-control.json
```

Use this transport with the Skeleton/Jeeves tool host, OpenAI API/Codex, or an
OpenAI plan that supports custom MCP write actions. No inbound network listener
is required for stdio MCP.

## Transport B: Plus-compatible Custom GPT Action

The Action API is a separate wrapper over the same bounded controller. It binds
only to `127.0.0.1:8765` and requires a root-only Bearer key for both state and
control endpoints.

Install the localhost service:

```bash
sudo scripts/install_home_media_action_api.sh \
  --repo-root /home/agent/agent-dev/repos/Skeleton
```

Then explicitly enable HTTPS exposure through Tailscale Funnel:

```bash
sudo scripts/enable_home_media_action_funnel.sh
```

Tailscale Funnel forwards public HTTPS to the localhost service. The API still
requires its independent Bearer key. The unauthenticated `/health` and
`/openapi.json` endpoints contain no Home Edge state or private configuration.

The Funnel step returns a stable `https://...ts.net` hostname. Generate the
Custom GPT Action schema with that hostname:

```bash
sudo /usr/bin/python3 \
  /home/agent/agent-dev/repos/Skeleton/scripts/home_edge_control_action_api.py \
  --print-openapi \
  --server-url https://REPLACE_WITH_FUNNEL_HOST
```

In the Custom GPT Action configuration:

1. paste the generated OpenAPI document;
2. select API-key authentication;
3. use Bearer authentication;
4. copy the value of `SKELETON_HOME_MEDIA_ACTION_API_KEY` from the private file
   `/etc/skeleton/home-media-action.env` once;
5. never paste the Home Edge HMAC secret, SSH key, profile, or raw receipts.

After saving the custom GPT, it can be invoked in another ChatGPT conversation
with `@` and then a natural-language command. The Action itself remains limited
to the mode enum and volume integer.

## Security boundaries

- The universal `home_edge_exec` tool is not exposed by the Custom GPT Action.
- The public API cannot accept arbitrary execution parameters.
- The API binds only to localhost; public TLS termination is owned by Tailscale
  Funnel.
- Mutation requests are signed by the trusted controller and audited by the
  installed Home Edge executor.
- The controller uses the existing Home Edge HMAC secret and SSH identity; no
  second executor identity is introduced.
- GitHub stores code and sanitized status only, never private addresses, keys,
  raw commands, or receipts.

## Disable public Action access

Disable Funnel without removing the local service:

```bash
sudo tailscale funnel reset
```

Disable the local Action API:

```bash
sudo systemctl disable --now skeleton-home-media-action.service
```
