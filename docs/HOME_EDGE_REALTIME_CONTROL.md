# Home Edge realtime control

## Purpose

The realtime path starts the existing `home_edge_exec` MCP server directly on the trusted Hetzner controller. It does not create GitHub issues and does not wait for Runner polling.

```text
Jeeves/Skeleton tool host
  -> stdio MCP launcher on Hetzner
  -> existing signed home_edge_exec client
  -> strict OpenSSH over Tailscale
  -> installed home_edge_exec on home-edge-01
```

The universal executor, signing contract, audit log and idempotency cache remain unchanged. No second executor, SSH identity or signing secret is created.

## Runtime inputs

The launcher reads the existing private files:

- `/etc/skeleton/home-edge-01.env`
- `/etc/skeleton/home-edge-executor-controller.env`

The MCP registration file contains only the launcher path. It contains no private values.

## Install on the trusted controller

```bash
sudo scripts/install_home_edge_realtime_controller.sh \
  --repo-root /home/agent/agent-dev/repos/Skeleton
```

The installer:

1. installs `/usr/local/bin/skeleton-home-edge-exec-mcp`;
2. installs `/usr/local/bin/skeleton-home-edge-exec-probe`;
3. installs `/etc/skeleton/mcp/skeleton-home-edge-exec.json`;
4. validates MCP `initialize` and `tools/list`;
5. performs one signed read-only `/usr/bin/true` call through Home Edge;
6. restores the previous installation if validation fails.

A persistent service is intentionally not installed. The actual tool host starts the stdio MCP process on demand, which removes an unnecessary network listener and service restart.

## Tool-host registration

Register the contents of `/etc/skeleton/mcp/skeleton-home-edge-exec.json` in the actual Jeeves/Skeleton MCP configuration and restart or reload that tool host once.

Expected registration:

```json
{
  "mcpServers": {
    "home-edge-exec": {
      "command": "/usr/local/bin/skeleton-home-edge-exec-mcp",
      "args": []
    }
  }
}
```

After registration, the tool host must show one tool named `home_edge_exec`.

## Health check

```bash
sudo /usr/local/bin/skeleton-home-edge-exec-probe
```

Successful output is a compact public-safe JSON object with:

- `status: ok`;
- `initialized: true`;
- `tool_listed: true`;
- `call_status: ok`;
- a receipt hash;
- measured controller-to-receipt latency.

Normal routine commands use this synchronous MCP path. GitHub remains an asynchronous audit and development system only.

## Current external boundary

Repository code can install and validate the standard stdio MCP server, but it cannot modify the configuration of a tool host that is not represented in the repository or connected to the deployment runtime. Direct ChatGPT/Jeeves control must not be claimed until the one-time MCP registration is visible in the actual tool host.
