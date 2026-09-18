# Skeleton Media extraction

The media implementation is now owned by the public repository `alanua/skeleton-media`.

## Ownership boundary

Skeleton keeps the control plane: project routing, approvals, registered executors, audit, memory routing, device/runtime authority and generic Home Edge contracts.

Skeleton Media owns media-domain implementation: Skeleton Cast, resolver/player/discovery, IPTV, media state, media clients, adaptive media remote, release monitoring and video-understanding code.

## Compatibility phase

Some media source still exists in Skeleton because current Runner/runtime-maintenance code imports it directly. Those copies are compatibility-only. New media feature work must target `alanua/skeleton-media`.

The compatibility copies must not be deleted until the remaining direct imports are replaced by an explicit cross-repository contract and both repositories pass CI.

## Production

Repository extraction did not change the live Home Edge installation.

The standalone Skeleton Media repository intentionally disables direct production deployment. A production cutover requires a registered Skeleton Home Edge operation, rollback, audit receipt and independent verification of the real media state.
