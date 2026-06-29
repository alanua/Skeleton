# home-edge-01

`home-edge-01` is documented here as a template registration for the house media
PC and universal local execution edge.

## Template Identity

```text
Node: home-edge-template-01
Tailscale IP: 100.64.0.10
User: home-edge-user
Controller: controller-template-01 / 100.64.0.20
Primary route: eth-template0 through 192.0.2.1
```

These are not live operator identifiers. Real endpoint values are supplied at
runtime through the local profile or environment variables listed in
`docs/HOME_EDGE.md`.

## Universal Scope

The node is intended for system administration, networking, routers, home
automation, containers, services, files and backups, browser and desktop
recovery, media work, USB hardware, ESP/WLED tooling, monitoring and logs.

Universal does not mean unrestricted shell. Each capability is exposed as a
typed action with a reviewed implementation and risk classification.

## Modem Role

A USB modem is optional. The gateway remains valid when no modem is physically
attached to the media PC. Modem observations are reported only when a live probe
sees the device.

## Deployment

Repository contracts are merged first. A separate approved runtime task then
activates and validates the fixed transport from the actual Runner service
context.
