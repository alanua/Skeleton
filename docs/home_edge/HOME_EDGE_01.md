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

## Reviewed modem maintenance

`home_edge_01_private_sim_unlock_o2_apn_test` is the bounded approved-mutation task for
the reviewed Huawei modem SIM-unlock and O2 APN test workflow. It requires the exact
runtime approval marker `APPROVE_HOME_EDGE_01_PRIVATE_SIM_UNLOCK_O2_APN_TEST` in addition
to the maintenance task id.

The public runner passes only a private inherited secret descriptor such as
`env:SKELETON_HOME_EDGE_01_MODEM_SIM_UNLOCK_PIN`; it must never read or report the secret
value. The runtime implementation must preserve the existing primary route and Tailscale
recovery path before and after the modem action, create the `internet` APN only as a
non-default profile with autoconnect disabled, and roll back the created modem
session/profile on any failed safety check. Public reports are aggregate status tokens
only.
