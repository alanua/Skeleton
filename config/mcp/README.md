`skeleton-control-hetzner.json` is the preferred standard stdio MCP registration fragment for the Hetzner controller. It exposes the Skeleton Control dispatcher, which routes named tools to existing ActionGate and runner-controller privileged gateway contracts and composes the Telegram read-only facade described below.

`skeleton-home-edge-exec.json` is the older direct Home Edge executor registration fragment. It contains only the installed launcher path; private Home Edge runtime values remain in `/etc/skeleton` on the trusted controller.

`core.telegram_mcp_readonly` provides the code-level Telegram read-only facade
that is composed into `skeleton-control-hetzner`. It lists exactly six
read-only tools: `resolve_source`, `get_history`, `get_message`, `search`,
`sync_source`, and `list_allowed_sources`. Production does not construct or open
a second Telegram reader, session, store, or source config; until the separate
runtime binding is available, these tool calls fail closed with
`BACKEND_UNAVAILABLE`. Raw Telegram secrets remain behind the Home Edge ->
Bitwarden provider callbacks.
