`skeleton-control-hetzner.json` is the preferred standard stdio MCP registration fragment for the Hetzner controller. It exposes only the minimal Skeleton Control dispatcher, which routes named tools to existing ActionGate and runner-controller privileged gateway contracts.

The same dispatcher also exposes the global Telegram read-only MCP tools from
`core.telegram_mcp_readonly`. Those tools must be bound to the existing
canonical Telegram reader runtime; MCP production registration does not load
Telegram source config, open a Telegram SQLite store, or create a Telegram
Gateway/session. Until that single runtime is bound, Telegram calls fail closed
with `BACKEND_UNAVAILABLE` and `telegram_backend_status` returns the public-safe
runtime-bind child issue details. The surface does not expose Telegram write,
watch, sync, shell, argv, env, source path, store path, secret, or session
parameters.

`skeleton-home-edge-exec.json` is the older direct Home Edge executor registration fragment. It contains only the installed launcher path; private Home Edge runtime values remain in `/etc/skeleton` on the trusted controller.
