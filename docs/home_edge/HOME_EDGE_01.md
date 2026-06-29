# home-edge-01

`home-edge-01` is the public synthetic name for the private house execution edge.

## Public identity boundary

```text
Node: home-edge-01
Tailscale IP: private
User: private
Controller: private
```

The default route is registered only in the private runtime profile. Runtime evidence must
verify it; repository registration is not treated as a live observation. GitHub reports may
show only aggregate route status such as `unchanged`, `review_required` or `unverified`.

## Universal scope

The node is intended for system administration, networking, routers, home automation, containers, services, files and backups, browser and desktop recovery, media work, USB hardware, ESP/WLED tooling, monitoring and logs.

Universal does not mean unrestricted shell. Each capability is exposed as a typed action with a reviewed implementation and risk classification.

## Modem role

A USB modem is optional. The gateway remains valid when no modem is physically attached to the media PC. Modem observations are reported only when a live probe sees the device.

The bounded GSM workflow uses a reserved non-default APN profile name only after a
fail-closed preflight proves that name is absent. The runtime captures the UUID created
by the current run and uses only that UUID for modification, activation, validation and
rollback deletion. A saved but inactive or disconnected profile is not a successful
connection test, and public Runner reports stay aggregate-only.

## Deployment

Repository contracts are merged first. A separate approved runtime task then activates and validates the fixed transport from the actual Runner service context.

Runtime execution must write diagnostics to an ignored/private artifact path outside the
public checkout, or run without persistence. The public template remains synthetic and is
not updated by local profile or environment override runs, even when those runtime values
match the synthetic template identity.
