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

## Deployment

Repository contracts are merged first. A separate approved runtime task then activates and validates the fixed transport from the actual Runner service context.

Runtime execution must write diagnostics to an ignored/private artifact path outside the
public checkout, or run without persistence. The public template remains synthetic and is
not updated by local profile or environment override runs, even when those runtime values
match the synthetic template identity.

## Network inventory boundary

`home-edge-01` uses the default gateway as its registered internet path. Any modem integrated
into that gateway is an expectation, not an observed Home Edge internal. An attached USB modem
is optional and does not determine node health.

Local-network inventory is not periodic. It runs only through the explicit read-only
`home_edge_01_lan_inventory_read_only` maintenance task. Detailed device records are private;
public reports are aggregate only.
