# Home Edge v1

Home Edge is the portable local gateway role for the home LAN.

The current carrier is `home-edge-01`, but the role must be movable to another mini PC without changing task logic.

## Split of responsibility

Hetzner/Skeleton is the brain: planning, queue, policy, memory and review.

Home Edge is the hands inside the home network: local reachability, device control, media control, service access and safe maintenance.

## Capability domains

1. Connectivity
2. Device Control
3. Network Admin
4. Service Hub
5. Human Interface
6. Provisioning and Recovery
7. Media and Presence

## Runtime rule

Tasks route by capability, not by hardcoded host.

The Device Registry is the source of truth for home capabilities. Discovery tools update the registry; they are not the source of truth.

`core/exo_device_control_plane.py` is the canonical EXO device-control plane for this rule. It keeps stable registry identities for `home_edge_01`, `samsung_tv` and `sharp_tv_living_room`, selects adapters by device capability, and gives each device its own transport supervisor. The Home Edge adapter delegates to the existing Home Edge executor gateway; it does not introduce a second executor.

Verification states are intentionally truthful:

- `dry_run` means no transport was called.
- `verified` means the selected transport returned a successful result.
- `recovering` means a transport is still inside a bounded recovery path.
- `blocked` means approval, capability, transport or recovery-budget checks stopped execution.

## Safety

Green tasks may run in dry-run or approved safe mode.

Yellow tasks wait for approval.

Red tasks require separate explicit approval and rollback evidence.

This document describes the safe code skeleton only. It does not deploy anything and does not operate live home devices.
