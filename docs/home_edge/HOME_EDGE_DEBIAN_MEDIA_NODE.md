# Home Edge Debian Media Node Bootstrap

`home_edge_01_debian_media_bootstrap_v1` is an exact allowlisted runtime-maintenance task for finishing the already installed Debian 13 media node on `home-edge-01`.

The task accepts only these non-empty runtime fields:

```text
Mode: RUNTIME_MAINTENANCE_TASK
Maintenance Task ID: home_edge_01_debian_media_bootstrap_v1
Repository: alanua/Skeleton
Expected Main SHA: <40 lowercase hex main SHA>
Operator Approval: EXPLICIT_FINISH_DEBIAN_MEDIA_NODE_20260805
Target: home-edge-01
```

Any other non-empty field that could affect command, package, path, user, host, service, timeout, lane, run identity, or script behavior is rejected before a Home Edge request is built.

The runner verifies the expected main SHA against the registered local `main` commit and `origin/main`, then sends a fresh signed `HomeEdgeExecRequest` through `core.home_edge.executor_gateway.execute_home_edge_request`. The task module does not provide direct SSH, subprocess transport, passwords, or arbitrary command execution.

The source-owned script installs only the fixed package list, configures LightDM autologin for `oleksii`, prepares Openbox, mpv, Chromium policy, and existing service enables for `ssh`, `lightdm`, and `avahi-daemon`. It never performs power, disk, bootloader, route, firewall, Tailscale, or private media mutations.

The public receipt reports aggregate counts and stable status codes only. Physical audio and video remain `physical_pending` until an operator observes them directly.
