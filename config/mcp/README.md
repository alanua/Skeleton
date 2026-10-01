`skeleton-control-hetzner.json` is the preferred standard stdio MCP registration fragment for the Hetzner controller. It exposes only the minimal Skeleton Control dispatcher, which routes named tools to existing ActionGate and runner-controller privileged gateway contracts.

`skeleton-home-edge-exec.json` is the older direct Home Edge executor registration fragment. It contains only the installed launcher path; private Home Edge runtime values remain in `/etc/skeleton` on the trusted controller.

`skeleton-telegram-readonly` is implemented by `core.telegram_mcp_readonly`.
It is a code-level stdio MCP dispatcher for the canonical Telegram gateway and
lists exactly six read-only tools: `resolve_source`, `get_history`,
`get_message`, `search`, `sync_source`, and `list_allowed_sources`. Runtime
registration should pass only source-config and store-path locations
(`SKELETON_TELEGRAM_SOURCES_CONFIG`, `SKELETON_TELEGRAM_STORE_PATH`); raw
Telegram secrets remain behind the Home Edge -> Bitwarden provider callbacks.
