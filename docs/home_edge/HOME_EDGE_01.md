# home-edge-01

`home-edge-01` is the house media PC and universal local execution edge.

## Fixed identity

```text
Node: home-edge-01
Tailscale IP: 100.127.35.74
User: valertos08
Controller: hetzner-agent-runner-1 / 100.69.215.63
```

The default route is registered as `enp1s0` through `192.168.1.1`. Runtime evidence must verify it; repository registration is not treated as a live observation.

## Universal scope

The node is intended for system administration, networking, routers, home automation, containers, services, files and backups, browser and desktop recovery, media work, USB hardware, ESP/WLED tooling, monitoring and logs.

Universal does not mean unrestricted shell. Each capability is exposed as a typed action with a reviewed implementation and risk classification.

## Modem role

A USB modem is optional. The gateway remains valid when no modem is physically attached to the media PC. Modem observations are reported only when a live probe sees the device.

## Deployment

Repository contracts are merged first. A separate approved runtime task then activates and validates the fixed transport from the actual Runner service context.
