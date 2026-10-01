`skeleton-control-hetzner.json` is the preferred standard stdio MCP registration fragment for the Hetzner controller. It exposes only the minimal Skeleton Control dispatcher, which routes named tools to existing ActionGate and runner-controller privileged gateway contracts.

When `/etc/skeleton/telegram_sources.yaml` is present on the trusted
controller, the same dispatcher also exposes the global Telegram read-only MCP
tools from `core.telegram_mcp_readonly`. Those tools reuse
`core.telegram_gateway.TelegramGateway` and the configured canonical Telegram
SQLite store; they do not introduce a second Telegram reader, session, or store,
and they do not expose Telegram write, watch, sync, shell, argv, env, source
path, store path, secret, or session parameters.

`skeleton-home-edge-exec.json` is the older direct Home Edge executor registration fragment. It contains only the installed launcher path; private Home Edge runtime values remain in `/etc/skeleton` on the trusted controller.
