`skeleton-control-hetzner.json` is the preferred standard stdio MCP registration fragment for the Hetzner controller. It exposes only the minimal Skeleton Control dispatcher, which routes named tools to existing ActionGate and runner-controller privileged gateway contracts.

`skeleton-home-edge-exec.json` is the older direct Home Edge executor registration fragment. It contains only the installed launcher path; private Home Edge runtime values remain in `/etc/skeleton` on the trusted controller.
